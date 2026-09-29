# 上游 `entity-type` 全集实测与静默丢弃(2026-09-29)

> **执行者**:本轮 Lead(独立完成,数据可复现)
> **上游**:`https://vip.kingdee.com/api/search`(匿名、零 cookie、零账号,与内核同链路)
> **纪律**:1 请求/秒、无并发、仅 GET;本轮 12 次检索请求 + 1 次取样,未写任何上游数据
> **判定口径**:直接读响应原始 JSON 的 `entity-type` 字段,不经任何归一化
> **关联**:`docs/adr/0014-清单粒度改帖子级.md`(只声明 `Answer` 一格)、
> `src/kd/_impl/_config.py:44`(`ENTITY_KINDS` 三值)、`src/kd/_impl/_upstream.py:94`(`return None`)

## 0. 一句话

**内核只认 3 个 `entity-type` 值,而上游至少给 6 个,认不出来的一律静默丢弃。**
「上游只有 3 个值」这个前提**从未被实测或文档证实过**——它是从少量偶发观测 + 测试合成样本
反推的假设,而 `_norm_item` 的 `return None` 是对该假设的硬编码信任。

**实测后果(端到端,真实上游响应)**:搜「微课」→ `total: 212` 但只返回 **2 条**,
`routeErrors: []`,`scanNote` 写「1/1 路完成」。10 条里 8 条课程被丢掉,并且**不留任何信号**。

## 1. `entity-type` 全集(12 词实测)

| 词 | 本次 `entity-type` 分布 |
|---|---|
| `信用额度控制` | Knowledge 9, Answer 1 |
| **`微课`** | **LearningCourse 8**, Article 2 ← 80% 被丢弃 |
| **`视频教程`** | Knowledge 3, Answer 4, Article 1, **LearningPath 1** |
| `学习路径` | Knowledge 5, Answer 2, Article 3 |
| **`直播`** | Knowledge 6, Answer 2, Article 1, **LearningCourse 1** |
| `专题` | Knowledge 8, Article 2 |
| `培训课程` | Knowledge 8, Answer 2 |
| `课程 视频` | Knowledge 8, Answer 2 |
| `发版说明` | Knowledge 10 |
| `考试` | Knowledge 3, Answer 5, Article 2 |
| `认证` | Knowledge 10 |
| **`题库`** | Knowledge 7, Answer 2, **KnowledgeSpecial 1** |

**合计 119 条结果,全集 6 种:**

| `entity-type` | 条数 | 内核认得? |
|---|---|---|
| `Knowledge` | 77 | ✅ → `knowledge` |
| `Answer` | 20 | ✅ → `question`(对外改名) |
| `Article` | 11 | ✅ → `article` |
| **`LearningCourse`** | **9** | ❌ **静默丢弃** |
| **`LearningPath`** | **1** | ❌ **静默丢弃** |
| **`KnowledgeSpecial`** | **1** | ❌ **静默丢弃** |

⚠️ **触发规律**:额外类型由**语义簇**触发,不是任意词都出现——
`微课`/`视频教程`/`直播` 触发课程类,`视频教程` 触发学习路径,`题库` 触发专题。
故它们在普通检索里不出现,**在"教学/视频/专题"类问题里集中出现**。

⚠️ **未知边界**:同轮另一个子代理报过第 7 种 `LearningBroadcast`(直播)4 条,
**本轮 12 词未重现**。故"全集"应读作"**至少 6 种,可能更多**"。
这不影响结论(3 个已知值之外确有未知值),但**影响"加一档 other 是否够"的判断**。

## 2. 归一化能力实测

`_manifest._norm`(`_manifest.py:72-79`)只做 ASCII `.lower()`,
`_upstream._norm_item`(`:42-94`)内部**零归一**,直接字面量比较:

| 输入值 | `_norm_item` | `_manifest._norm` |
|---|---|---|
| `'Knowledge'` | OK | OK |
| `'knowledge'` | OK | OK |
| `'KNOWLEDGE'` | OK(靠上层 lower) | OK |
| `'  Knowledge  '` | **None(丢弃)** | **None(丢弃)** |
| `'Knowledge\n'` | **None(丢弃)** | **None(丢弃)** |
| `'Ｋｎｏｗｌｅｄｇｅ'`(全角) | **None(丢弃)** | **None(丢弃)** |
| `'LearningCourse'` | None | None |
| `'learningcourse'` | None | None |
| `'KnowledgeSpecial'` | None | None |

**关键脆弱点**:`_norm_item` 的形参契约写着「`et` 是上游的 `entity-type`(**已小写**)」
(`_upstream.py:43`),**完全依赖唯一调用方遵守**,函数内部无任何防御。
把 `_manifest.py:78` 的 `.lower()` 去掉,`_norm_item` 会退化为大小写敏感、**全量静默丢弃**
——而 48 条离线用例**测不出来**:`t_type_vocab_mapping`(`tests/kd_regression.py:518/522`)
直调 `_norm_item` 时自己传小写,绕过了 `_norm` 这一层。

**两份归一不是重复实现,而是互补但分层脆弱**:一个什么都不做,一个只做 `.lower()`。
`strip()`、NBSP、全角折叠(NFKC)、尾换行**三层都不管**。

## 3. 端到端实证(绕过网络,注入真实上游响应)

把**真实**的「微课」响应整份注入唯一网络出口,跑完整链路:

```
真实响应 entity-type: ['Article','LearningCourse'×8,'Article']
totalElements = 212

=== 端到端 ===
total        = 212
results 条数  = 2            ← 10 条里只落下 2 条
落下的类型    = ['article','article']
routeErrors  = []           ← 空:调用方看不到任何异常
scanNote     = 多路清单:1/1 路完成,每路 pageSize=10,归并后 2 条(帖子级)
```

**这就是"说搜到了 212 条但一条都不给你,还不报错"的完整形态。** 三处矛盾同时出现:

- `total: 212` 与 `results: 2 条` —— 差 210 条没有任何解释
- `scanNote` 声称「1/1 路完成」—— 该路确实完成了,**是归一化层把结果扔了**
- `routeErrors: []` —— 路级无错,故错误通道为空,而条目级丢弃**没有自己的通道**

**后果**:调用 LLM 看到「212 条命中、无匹配」,会告诉用户"官方没这类资料"——
而库里其实有 8 条课程 + 2 条文章。这是本项目最忌讳的失效形态:
不是报错,是**把"我们没读懂"伪装成"上游没有"**。

## 4. 现状为什么没被发现

| 环节 | 现状 |
|---|---|
| 测试 | `tests/` 下**没有任何真实响应 fixture**;回归样本是合成的,只造 `Answer`/`Article`/`Knowledge` 三个值(`tests/kd_regression.py:864/872/879/1012/2209`) |
| 文档 | `CONTEXT.md:25` 与 `docs/adr/0014:74` 只声明 `Answer` 一格,**不是全枚举**;`_config.py:44` 的 `ENTITY_KINDS` 是**本套件对外**词汇表,不是上游枚举 |
| 仓库内唯一真值 | `docs/research/2026-09-06-school-course-sample.json`(全部 `LearningCourse`×10),但它来自 `/schoolapi/search`,当时被记为"课程在别的端点、与主链路无关"——**该推论不成立**:`/api/search` 自己就会返回 `LearningCourse`(本文件 §1 实测) |
| 巡检 | 无。丢弃路径无日志、无计数、无字段 |

## 5. 额外类型的可点链接与详情端点(实测)

| 类型 | 网页 URL 形式 | 实测 | 详情端点 |
|---|---|---|---|
| `LearningCourse` | `/school/detail/<id>` | ~~**200 可点**~~ → **❌ 生死判反** | ✅ `/schoolapi/rest/course/<id>`(返回 `data` 129 键,含逐字稿全文) |
| `LearningPath` | `/school/learnPath/<id>` | **未确认**(两次实测均落 `/error/404`) | ❌ 无 |
| `KnowledgeSpecial` | `/knowledge/specialDetail/<id>` | **依 id 而异**(1 活 3 死) | 未测 |
| `LearningBroadcast` | 上游自带 `url` 指向 `live.vhall.com`(**第三方域**) | **已复现;第三方域可点** | 未测 |

> ⚠️ **2026-09-29 更正注(实施轮追加,正文不回改)**
>
> 本表的 `LearningCourse` 行**判反了**,另两行也在同日复验中被更新。详见
> `docs/research/2026-09-29-额外类型链接形式复验.md`:
>
> * **`/school/detail/<id>` 恒死**(3 真 id × 2 轮,`url_effective` 全是 `/error/404`)。
>   原记录写"200 可点",踩的是本仓**陷阱 1**:`/error/404` 页**本身返回 200**,
>   只看首跳状态码必然把失效判成可点。补测 8 种候选网页形式,**全部死**。
> * `LearningBroadcast` **已用「公开课直播」复现**;其 `url` 指向第三方域
>   `live.vhall.com`,该域可点,但与 vip 域政策不是一回事。
> * `KnowledgeSpecial` 的 `/knowledge/specialDetail/<id>` **对部分 id 活、部分 id 死**
>   (严格交替复验 6 轮确认非限流假阳性),不成立为"路径可点"的常量。
>
> **对实施的影响**:`other` 档**不能给网页链接**(§6 的"收进来就能给链接"不成立),
> 但"收进来"本身照做——它治的是静默丢弃,与链接是两件事。

⚠️ **课程 URL 的单条样本风险**:`/school/detail/<id>` 只测了 1 个真 id + 1 个伪 id 对照。
按本仓已记录的判定纪律(见 `docs/research/2026-09-29-问答帖链接形式实测.md` §2 的四个坑),
**单一观测不足以定性**,下一轮实施前须多轮次复验。

## 6. 结论与下一轮实施要点

**已确定**:上游存在 3 个已知值之外的 `entity-type`,内核静默丢弃,且丢弃无任何信号。
这是一个**真实缺陷**,不是文档措辞问题。

**用户已裁定**:
1. 在现有三档之外**新增「其他」档**(对外类型名 `other`),收容这些罕见类型
   ——理由:用户判定这些类型「质量很低,得和前面三种类型区分开来」,
   而事实上它们**确实不是同一种资料**(课程是视频/PPT 课件,`resourceType` 为
   `video`/`document`/`ppt`;学习路径是一串课程的目录)。
2. 「其他」档**默认隐藏,需要时才显**(新增开关),且**告知跳过了多少条**
   ——与用户已拍板的「不能静默」纪律一致。
3. 标签归一**加上宽容层**(`strip` + `lower`,容忍前后空白/尾换行/全角/NBSP)。

**实施前必须先定的事实**:
- `LearningPath` 的可点形式**未确认**——它决定收进来之后能否给链接。
  若确认不了,该类型的处理须与课程分开裁定。
- `LearningBroadcast` 的 `url` 是**第三方域**(`live.vhall.com`),`linkPolicy` 需单独裁定,
  不能套用 `vip.kingdee.com` 的政策。

> ⚠️ **2026-09-29 更正注(实施轮追加)**:上两条已复验完毕,**结论是"额外类型一律不给网页链接"**。
> `LearningCourse` 的 `/school/detail/<id>` 恒死(原判定反了),`LearningPath` 仍未确认,
> `KnowledgeSpecial` 依 id 而异,`LearningBroadcast` 是第三方域。详见
> `docs/research/2026-09-29-额外类型链接形式复验.md`。故 `other` 档落 `no-link`,
> 而"收进来"照做(它治的是静默丢弃)。
- 实施时须同步:新增对外类型 → `ENTITY_KINDS`、`contract.json` 的 `resultKeysByType`
  与 `linkPolicy`、`read` 的 `_DETAIL_FN` 白名单。
  漏掉任一处会让「其他」档在某一层再次静默丢弃——**用一处静默换另一处静默**。
  ⚠️ **2026-09-29 更正**:原文此处还列了 `_URL_OF` 模板(第四处),该项**已撤销** ——
  `other` 恒 `no-link`,该模板实测为生产死键(哨兵实验),已删除,故同步点由四处收为三处。
- 验收须含**一条真实响应的 fixture**(当前仓库一条都没有),否则同一个缺陷会再次复现。
