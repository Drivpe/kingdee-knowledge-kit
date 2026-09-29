# 票 #32:新增 other 档 —— 不再静默丢弃罕见 entity-type

状态: ✅ 完成(2026-09-29) | 依赖: #31(标签宽容层,同一处归一逻辑)
| 前置: 步骤 0 链接复验**已完成** | 优先级: P1(最大改动面)
验收依据: 用例 `t_other_tier`(真实上游响应 fixture `tests/fixtures/upstream-search-培训课程.json`,8 条 `LearningCourse` 默认隐藏且 `otherSkipped` 通报)、`t_other_dedupe_key`(C1 跨类型去重键并入 `upstreamType`)、`t_other_link_policy_has_consumer`(H2 政策消费者),见 docs/specs/2026-09-29-验收记录.md
spec: `docs/specs/2026-09-29-内核行为出入修复与交互提速-施工规格.md` §D8
依据: `docs/research/2026-09-29-上游entity-type全集与静默丢弃.md`
     + `docs/research/2026-09-29-额外类型链接形式复验.md`(**本轮新增**)

## 问题

**内核只认 3 个 `entity-type` 值,而上游至少给 6 个,认不出来的一律静默丢弃。**

12 词实测 119 条,全集 **6 种**:`Knowledge 77 / Answer 20 / Article 11 /
LearningCourse 9 / LearningPath 1 / KnowledgeSpecial 1`。后端到端**真实上游响应**实证:

```
搜「微课」→ 真实响应 entity-type: ['Article','LearningCourse'×8,'Article']
total        = 212
results 条数  = 2            ← 10 条里只落下 2 条
routeErrors  = []           ← 空:调用方看不到任何异常
scanNote     = 多路清单:1/1 路完成,每路 pageSize=10,归并后 2 条(帖子级)
```

**三处矛盾同时出现**,且这是本项目最忌讳的失效形态:**不是报错,是把"我们没读懂"
伪装成"上游没有"**。调用 LLM 看到"212 条命中、无匹配",会告诉用户"官方没这类资料"
—— 而库里其实有 8 条课程 + 2 条文章。

**为什么长期没被发现**:`tests/` 下**没有任何真实响应 fixture**,回归样本是合成的、
只造 `Answer` / `Article` / `Knowledge` 三个值。

## 要建的

### 1. 新增对外类型名 `other`

收容 3 个已知值之外的 `entity-type`。用户裁定理由:这些类型**确实不是同一种资料**
(课程是视频/PPT 课件,`resourceType` 为 `video`/`document`/`ppt`;学习路径是一串课程的目录),
且用户判定它们「质量很低,得和前面三种类型区分开来」。

⚠️ 注意:12 词之外还复现过第 7 种 `LearningBroadcast`(票内已实测复现)。
故"全集"应读作"**至少 6 种,可能更多**" —— 这正是要"收容未知值"而不是"枚举已知值"的理由。

### 2. 默认隐藏 + 新增开关 + 告知跳过了多少条

- **默认隐藏**:罕见类型不混进主清单(保护挑选信噪比);
- **新增开关**放出;
- **隐藏时告知跳过了多少条** —— 与项目既有纪律「不能静默」一致。

### 3. 四处同步(**漏一处就是"用一处静默换另一处静默"**)

1. `ENTITY_KINDS`(对外类型白名单);
2. `contract.json` 的 `resultKeysByType`(新增 `other` 一档)与 `linkPolicy`(新增 `other`);
3. `read` 的 `_DETAIL_FN` 白名单。

⚠️ **2026-09-29 更正:原第 4 项「`_URL_OF` 模板」已撤销** —— `other` 档恒 `no-link`,
该模板被实测确认为**生产死键**(哨兵实验:替换后从未出现在输出里),已删除。
表里只放恒有链接的三档。详见 `docs/specs/2026-09-29-验收记录.md` §7。

### 4. 链接政策:`other` 落 `no-link`

**这是本轮复验的直接后果,推翻了上一份文档的结论**
(`docs/research/2026-09-29-额外类型链接形式复验.md`):

| 类型 | 网页形式 | 判定 |
|---|---|---|
| `LearningCourse` | `/school/detail/<id>` | **死**(3 真 id × 2 轮,`url_effective` 全是 `/error/404`) |
| `LearningCourse` | 另 8 种候选形式 | **全部死** |
| `LearningPath` | 未确认 | 未确认 |
| `KnowledgeSpecial` | `/knowledge/specialDetail/<id>` | **依 id 而异**(1 活 3 死;严格交替复验 6 轮排除限流) |
| `LearningBroadcast` | 上游自带 `url` → `live.vhall.com` | **第三方域**,与 vip 域政策不是一回事 |

⚠️ 上一份文档记的 `/school/detail/<id>` "200 可点"**判反了** —— 那是踩了本仓**陷阱 1**:
`/error/404` 页**本身返回 200**,只看首跳状态码必然把失效判成可点。

即**额外类型没有任何一个网页形式被多轮次确认为活的**。按 `linkPolicy` 的保守方向
(少给一个链接,不误导读者),落 `no-link`。

### 5. `read` 侧:进白名单,但**不给全文**

- `other` **不进** `_DETAIL_FN`;
- `read(kind="other")` 必须给出**准确的**错误提示 —— 区分"非法 kind"与
  "合法但本档没有全文端点",**不得**沿用 `bad_kind` 那条会把人引到"我传错了"方向的 hint。

### 6. fixture 要求(验收硬条件)

**须含一条真实上游响应 fixture** —— 当前仓库一条都没有,这正是本缺陷长期未被发现的根因。
可用仓库内既有的 `docs/research/2026-09-06-school-course-sample.json`
(全部 `LearningCourse`×10)作真实数据来源,但**注入路径须经真实出口**,不得只调内部函数。

### 7. 实现时须知道的 id 形状事实

课程条目的 `id` 有**两种形状**,同一次响应内混排:

| 形状 | 样本 |
|---|---|
| **小整数** | `6898` / `6896` / `6897` / `6899` |
| 雪花串 | `200641239941605888` / `513283988409192192` |

上游另给 `xId`,形如 `"LearningCourse-6898"`(带类型前缀)。
**不得按长度或字符集判 id 形状**。

## 验收标准

- [ ] 上游的 3 个已知值之外的 `entity-type` **不再被静默丢弃**
- [ ] 新增对外类型 `other`,**默认隐藏**
- [ ] 新增开关可放出 `other` 条目
- [ ] 隐藏时返回体**写明跳过了多少条**(不得静默)
- [ ] **三处**同步齐全(`ENTITY_KINDS` / `resultKeysByType` / `_DETAIL_FN`),且**每处都有用例覆盖**
      ⚠️ 2026-09-29 由「四处」改为「三处」:第 4 项 `_URL_OF` 模板已撤销(死键,已删)
- [ ] `linkPolicy.other` 为 `no-link`,且代码真的读它
- [ ] `read(kind="other")` 的提示**准确**区分"非法 kind"与"本档无全文端点"
- [ ] **含一条真实上游响应 fixture**,且经真实出口注入(不是只调内部函数)
- [ ] 用「微课」这类课程簇词做端到端验证:`total` 与 `results` 的差额**有解释**
- [ ] 离线回归 48/48 保持绿
- [ ] `git status` 确认未触碰用户既有的 31 项未提交改动

## 不在本票范围

- `other` 档的**全文获取**。`LearningCourse` 的接口端点
  (`/schoolapi/rest/course/<id>`)实测可用且含逐字稿(样本 3135 / 6542 / 17935 字符),
  但那是另一档工作,且 `LearningPath` 无端点、`LearningBroadcast` 在第三方域。
  本票只做"不再静默丢弃"。
- `LearningBroadcast` 的**第三方域链接政策**(全部证据建立在 vip 域上,不能套用)。
- `KnowledgeSpecial` 依 id 而异的可点性(与按 kind 一刀切的政策形状不兼容)。
