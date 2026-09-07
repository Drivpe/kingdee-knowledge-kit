# 调研:产品线路由串线 与 引用链接匿名不可达(2026-09-06)

> 起因:2026-09-06 真实使用(旗舰版 BOM 维护表单标识)暴露两个缺陷——
> ① agent 中途改用 `kd search "ENG_BOM" --product 0`(0=不过滤),企业版问答被当旗舰版答案输出;
> ② kd 返回的 `vip.kingdee.com/question|article|knowledge/{id}` 引用链接匿名访问全部 302 到登录页,最终用户点不开。
> 方法:源码定位(service/cli/skills/docs 全链路)+ 本地服务实测 + 匿名 curl 实测(总计约 15 个上游请求,人类频率,全程零 cookie)。
> 附:会话收尾 usage 文档(`~/.lingeebuild/corpus/usage/2026-09-06-bom维护-开发平台表单.md`)已记录本次误引与修正。

## TL;DR

| 缺陷 | 根因一句话 | 推荐修复 |
|---|---|---|
| 产品线串线 | 三层叠加:ask 显式关键词模式(**--kw**)存在硬 bug 会**静默丢弃 --product 过滤**(service 早期 return);结果对象的 products 字段未透传到 ask 资料包 sources 顶层;ANSWER-SPEC/SKILL 无「product 路由粘性 + 引用前核对 products」规则 | 修 --kw bug + sources 顶层透传 products(服务端,小)+ SPEC/SKILL 加硬性规则(小) |
| 链接匿名不可达 | kd 构造的是**网页 URL**,而网页路由匿名一律 302 登录墙;kd 能匿名读全文靠的是另一套 **JSON API**(网页与 API 同源不同门) | ANSWER-SPEC 引用规范改为「标题+类型+ID+检索方式」为主、链接标「(需登录)」(小)+ 服务端静态规则标注 `link_public`(小) |

---

## 缺陷 1:产品线内容串线

### 1.1 --product 全链路传递(源码梳理)

**CLI 层(cli/kd.py)**:

| 命令 | 传递点 | 说明 |
|---|---|---|
| `kd search` | cli/kd.py:56-58 `if a.product: qs["productId"] = a.product` | **默认 93**(cli/kd.py:223);注意 `--product 0` 时 `a.product=0` 为 falsy,参数被整个丢弃——但语义上服务端「缺参数=不过滤」,与 0 的文档语义(0=不过滤)恰好等价,不算错,但属于**靠巧合成立**的双层约定 |
| `kd ask` | cli/kd.py:85 `if a.product is not None: body["productId"] = a.product` | 默认 None(cli/kd.py:242),判空用的是 `is not None`,0 会正常透传 |
| `kd ai` | cli/kd.py:183 | 同 ask,默认 None |

**服务层(service/kingdee-ksearch-service.py)**:

- `/search` 入口:service/kingdee-ksearch-service.py:1121 `product_id=(qs.get("productId") or [None])[0] or (body or {}).get("productId")`;
- 上游过滤:service/kingdee-ksearch-service.py:506-507(**0 与 None 等价,必须省略参数**):

```python
if product_id and int(product_id) != 0:  # 0=不过滤:必须省略参数,传 0 上游会当真值过滤(实测把 Knowledge 挤出前排)
    params["productIds[0]"] = int(product_id)
```

- **ask 多路拆解的路由如何携带 productIds**(`plan_routes`,service/kingdee-ksearch-service.py:770-861):
  - 非关键词模式:产品词先由 `productAliases` 从问句推导(query_routes.json `productAliases.alias`:旗舰版→93 等,service/kingdee-ksearch-service.py:790-794);然后**所有路由统一携带同一个 productIds**(不是只有 kind=product 路携带,product 路只是截断时保席位):

```python
# service/kingdee-ksearch-service.py:852-855
if product_id and int(product_id) != 0:
    for r in routes:
        r["productIds"] = int(product_id)
```

  - 每路检索按路由自带字段取过滤(service/kingdee-ksearch-service.py:906):`knowledge_search(r["terms"], product_id=r.get("productIds"), ...)`。

### 1.2 根因 1(硬 bug):`--kw` 显式关键词模式静默丢弃 --product

`plan_routes` 的 keywords 分支在**加 productIds 的代码块之前**提前 return(service/kingdee-ksearch-service.py:781-788):

```python
if keywords:  # 调用方显式关键词:每词一路(保持 v5 兼容语义)
    routes, seen = [], set()
    for k in keywords:
        ...
        routes.append({"kind": "explicit", "terms": k, "why": "调用方显式关键词"})
    return routes[:max_routes], product_id   # ← 提前返回,852-855 的 productIds 注入永远执行不到
```

而 `ask_bundle` 每路只读 `r.get("productIds")`(service/kingdee-ksearch-service.py:906)——于是 **`kd ask --kw ... --product 93` 的 93 被静默丢弃,所有路不过滤**。

**本地实测复现**(2026-09-06,服务 v6.1,部署副本与仓库 diff 一致):

```bash
kd ask "旗舰版BOM维护表单标识" --kw "BOM维护 表单标识" --product 93 --topk 1
# routes: [{"kind":"explicit","terms":"BOM维护 表单标识",...}]        ← 无 productIds 字段
# source: answer 640173554994156800
#   detail.products: ['企业版', '标准版', '生产制造', '工程数据管理']   ← 明确带了 --product 93 却召回企业版问答
```

本次事故 agent 没走 --kw,但同一份资料包里混入企业版内容的机制通道是同一个:**产品过滤缺失/被绕过时,企业版内容量占优会把旗舰版内容挤出候选**(query_routes.json productAliases.note 也写明「实测无产品过滤时字段名路会被全产品噪声淹没」)。

### 1.3 根因 2:结果对象的产品线字段存在但不显眼、不强制

上游**本身携带每条结果的产品线信息**,且已部分透传,但没有任何规范要求 agent 核对它:

| 位置 | products 字段 | 语义 |
|---|---|---|
| search 结果 knowledge/article 条目(service/kingdee-ksearch-service.py:475/497,取自上游 `classifies`) | 有,如 `["企业版/标准版"]` | **产品线级**,足以甄别 |
| search 结果 answer 条目(service/kingdee-ksearch-service.py:488) | 弱,常是模块名(实测:`["工程数据管理"]`、`["插件开发"]`) | 模块级,**不一定含产品线** |
| read/ask 深读 detail(question_detail→`_q_products`,service/kingdee-ksearch-service.py:650-654/662;knowledge_article:630;article_detail:712) | 有,问答的 module pathName 按 `/` 拆分后**首段即产品线**(实测:`['企业版','标准版','生产制造','工程数据管理']`) | 产品线级,但藏在 `sources[].detail.products`,**sources 顶层没有该字段**(ask_bundle 组装 sources 的 943-948 行只放 rank/type/id/title/url/snippet/fusedScore/detail) |
| `kd search` 输出 | 有(本次实测 `kd search "ENG_BOM" --product 0 --size 5`,前两条 article 的 products 均为 `["企业版/标准版"]`) | **事故发生时甄别所需的数据其实在返回体里,agent 没看,规范也没要求看** |

### 1.4 根因 3:规范层缺「产品路由粘性 + 引用前核对」硬性规则

- `docs/ANSWER-SPEC.md` 全文(35 行)没有任何涉及产品线的条款——第 18 行引用规则只管 `[n]` 格式,第 20 行只区分「官方/社区」权威级;
- `skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md` 第 116 行只写了 `--product 0=不过滤` 的参数说明,第 108-122 行「手动细粒度调试命令」一节没有「会话内 product 路由必须保持粘性」「引用前核对 results/products 字段」的约束;
- SKILL.md:151 甚至有「不要在 ask 之外临场编检索词路」的禁令,但**换 product 路由**这条临场变道没有任何约束——事故正是从 ask(--product 93)变道到 search(--product 0)发生的。

### 1.5 修复方案与评估

| 选项 | 内容 | 改动文件 | 工作量 | 评估 |
|---|---|---|---|---|
| **A1(推荐①)** | 修 `--kw` 丢过滤 bug:keywords 分支 return 前同样注入 `productIds`(或在 `ask_bundle` 里对 `r.get("productIds")` 回退到全局 `product_id`) | service/kingdee-ksearch-service.py:781-788 或 906 | 小 | 一处改动消灭一条静默串线通道,必做 |
| **A2(推荐②)** | sources 顶层透传 products:ask_bundle 组装 source 时加 `"products": (d or item).get("products")`;search 已有无需改 | service/kingdee-ksearch-service.py:943-948 | 小 | 把甄别所需数据从 `detail.products` 提到第一眼可见处 |
| **B(推荐③)** | SPEC/SKILL 加硬性规则:「同一问题会话内 product 路由粘性,中途不得改弱(0/不过滤);引用任何结果前必须核对其 products 字段与目标产品线一致,冲突内容标注来源产品线或弃用」 | docs/ANSWER-SPEC.md(新增第 7 条)、skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md(手动命令节+禁止事项) | 小 | 纯文档,直接对本次事故的临场变道行为设卡 |
| C | 软提示:search 请求 `--product 0`/未过滤时,CLI 向 stderr 输出 warning JSON(`{"notice":"product=0 未过滤,结果可能混入其他产品线,引用前核对 products 字段"}`);或支持 `KD_PRODUCT` 环境变量做会话级粘性 | cli/kd.py:56-61 | 小-中 | 有价值但优先级低于 A1/A2/B;env 粘性对 agent 是否生效取决于 harness,可靠性不如 B 的明文规则 |

**推荐排序:A1 → A2 → B(可同批落地,合计半天内);C 视需要跟进。**

---

## 缺陷 2:引用链接匿名不可达

### 2.1 kd 使用的匿名 API 与网页 URL 的对应关系(源码梳理)

kd 服务匿名拉全文用的**全是 JSON API**,而输出给调用方的 `url` 字段却是**网页路由**——两者同源(vip.kingdee.com)但访问控制不同:

| 实体 | 匿名 API(实测 200,零 cookie) | kd 输出的网页 URL(实测 302 登录墙) | 构造点 |
|---|---|---|---|
| 检索 | `GET /api/search?text=&productIds[0]=` | — | service/kingdee-ksearch-service.py:508 |
| knowledge | `GET /knowledgeapi/knowledge/{id}` | `https://vip.kingdee.com/knowledge/{id}` | service/kingdee-ksearch-service.py:626 ↔ 629(同样构造见 472/400/312) |
| answer | `GET /api/questions/{id}`(+`/answers`、`/api/answers/{id}`) | `https://vip.kingdee.com/question/{id}` | service/kingdee-ksearch-service.py:656/674/682 ↔ 659(同构造见 482/703) |
| article | `GET /api/articles/{id}` | `https://vip.kingdee.com/article/{id}` | service/kingdee-ksearch-service.py:707 ↔ 711(同构造见 494) |
| 官方 AI 分享 | `GET /aisapi/ai-search/sharing-chats/{chatId}` | 分享短链 `https://vip.kingdee.com/link/s/{code}` | service/kingdee-ksearch-service.py:997 ↔ 972-994(短链仅用于解析出 chatId) |

URL 模板统一集中在 `_CORPUS_URL`(service/kingdee-ksearch-service.py:312)与 `_norm_item`/各 detail 函数;落地缓存 front-matter 的 `url:` 也复用同一模板(service/kingdee-ksearch-service.py:400),所以 landing 文档里 rg 出来的引用链接同样是登录墙 URL。CLI 侧 `kd ai` 的 references(cli/kd.py:202-203)与 ask sources 的 url 原样透传,无任何加工。

### 2.2 curl 实测证据(2026-09-06,浏览器 UA `Mozilla/5.0 ... Chrome/152.0.0.0`,零 cookie,请求间 sleep 2-3s,合计约 15 请求)

| # | URL(示例 ID 均来自本次/历史真实检索命中) | 状态码 | 落地/重定向 | 结论 |
|---|---|---|---|---|
| 1 | `vip.kingdee.com/question/799346568250934528` | 302 | `/api/account/login-url?state=<REDACTED base64 含 /question/799346568250934528?islogin=true>&display=web` | 登录墙 |
| 2 | `vip.kingdee.com/article/56784392135739905` | 302 | 同上形态(`/article/...`) | 登录墙 |
| 3 | `vip.kingdee.com/knowledge/402990431979506944` | 302 | 同上形态(`/knowledge/...`) | 登录墙 |
| 4 | `vip.kingdee.com/link/s/clzL8`(官方 AI 分享短链,逐跳跟踪+cookie jar) | 302→302→302→302→302→200 | hop1 `/searchchats/884194634405046272?sharingId=...&productLineId=40...` → hop2 `/error/404?productLineId=40` → hop3 `/api/account/login-url?...` → hop4/5 `passport.kingdee.com` OAuth → 200(登录页) | 短链第一跳正常解析出 chatId,但**目标页 /searchchats 匿名也是 404+登录墙**;「短链可匿名打开」在 curl 下不成立(浏览器端是否靠前端渲染存活待验证,但 kd share 不依赖页面) |
| 5 | `vip.kingdee.com/link/s/lxHDp` | 302 | `/article/248777993676668672?sharingId=...&get_from=article-id&...` | 短链解析到 article 页 → 落入 #2 的登录墙 |
| 6 | `vip.kingdee.com/api/questions/799346568250934528` | 200 | JSON 全量问题数据(creator/createdAt/answers 等) | **匿名 API 存在且无鉴权**——kd 读全文的通道 |
| 7 | `vip.kingdee.com/aisapi/ai-search/sharing-chats/884194634405046272` | 200 | 34,964 字节 JSON(分享对话全文) | kd share 实际端点,匿名 |
| 8 | `vip.kingdee.com/knowledgeapi/knowledge/402990431979506944` | 200 | 全文 JSON;字段含 `productLineId`/`otherProductLineIds`/`entityUrl`/`sources`/`attachments`,本例 `products:['企业版/标准版']` | 匿名;`entityUrl`/`sources` 字段是「知识文档→站内其他形态(如帮助中心)映射」的候选线索(本次未深挖) |
| 9 | `help.open.kingdee.com/` → `/dokuwiki/doku.php` | 302→200 | 官方帮助中心是匿名 dokuwiki | **匿名可访问的替代信源存在**,但 knowledge id→wiki 页面无稳定映射(需按标题搜 wiki,见 SKILL.md:83 已有 `site:help.open.kingdee.com` 第三跳) |

**根因**:kd 把「匿名 JSON API 可达」错误外推成了「网页 URL 可达」——URL 模板(`_CORPUS_URL`/`_norm_item`)构造的是网页路由,而该站网页路由对匿名会话一律 302 `login-url`。**没有任何 URL 形态的正文页能匿名打开**(知识正文唯一匿名通道就是 API 本身)。

### 2.3 修复方案与评估

| 选项 | 内容 | 改动文件 | 工作量 | 评估 |
|---|---|---|---|---|
| **B(推荐①)** | 修改 ANSWER-SPEC 引用规范:参考来源默认给「`[n] 标题 —— 类型(type/id,检索方式)`」,如 `BOM维护 —— 官方文档(knowledge/272106361545148416,`kd read 272106361545148416` 可读全文)`;如需给 URL 必须标注「(需登录)」 | docs/ANSWER-SPEC.md(第 18 行引用条款扩写)、skills/.../SKILL.md(回答规范节同步一句)、cli/kd.py(AI_SPEC_PROMPT 第 3 条同步,cli/kd.py:115) | 小 | 零上游请求、立即止血;ID+检索方式对 agent 消费者完全够用(人要验证时可自己跑 kd read) |
| **C(推荐②)** | service 静态标注 `link_public`:在 `_norm_item`/各 detail 函数按 URL 前缀规则表直接写 `link_public:false`(question/article/knowledge 三类全部已知登录墙,share refs 的 `/link/s/` 标 false),**零额外上游请求**——实测已证明三类网页 100% 登录墙,无需逐条探测 | service/kingdee-ksearch-service.py:466-500(_norm_item 三分支)、629/659/703/711(detail 四函数)、1001-1004(share refs) | 小 | 让调用方 agent 拿到机器可读信号,不依赖其读过 SPEC;注意**不要**做逐条 HEAD 探测(每条结果一次上游请求,违反匿名链路频率纪律) |
| **A(可选③)** | `kd checkurl <url...>`:批量 GET(-o /dev/null,非 HEAD,该站对 HEAD 行为未验证),302 且 Location 含 `/api/account/login-url` → 判「需登录」;`kd read --verify` 同理对本次结果校验 | cli/kd.py(新增 cmd_checkurl + parser) | 小-中 | 工具化兜底,适合 landing 缓存里历史文档链接的批量体检;但有了 C 的静态规则后日常价值下降 |
| **D(不推荐作主案)** | 优先引用匿名替代链接:帮助中心(help.open.kingdee.com,匿名 200)按标题映射;分享短链需官方 AI 生成、不可构造且实测其落地页同样登录墙(#4/#5) | service/(映射逻辑)、SKILL.md 第三跳 | 中-大 | 帮助中心映射不稳定(knowledge id→wiki 页无确定关系)、覆盖面远小于社区库;仅保留为 knowledge 类的补充探索(`knowledgeapi/knowledge` 返回的 `entityUrl`/`sources` 字段值得后续挖一次) |

**推荐排序:B → C(可同批落地,合计半天内)→ A(锦上添花);D 降级为后续探索项。**

---

## 引用来源

**源码(行号以 2026-09-06 仓库为准;已 diff 确认部署副本 `~/.kingdee-kit/bin/kd.py`、`~/.kingdee-kit/service/kingdee-ksearch-service.py` 与仓库逐字节一致):**

- 缺陷 1:cli/kd.py:56-58、83-85、223、242;service/kingdee-ksearch-service.py:506-507、781-788(--kw 提前 return)、790-794、852-860、897、906、943-948、1121、1159-1162;service/query_routes.json(productAliases.note、displayNote);docs/ANSWER-SPEC.md:18-20;skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md:108-122、148-154
- 缺陷 2:service/kingdee-ksearch-service.py:63、312、400、466-500、472/482/494、508、626-631、650-663、659、696-704、706-713、711、972-994、997、1001-1004;cli/kd.py:115、189-191、202-203

**实测命令(2026-09-06,零 cookie,UA=Chrome/152,请求间隔 2-3s):**

```bash
# 缺陷 1(本地服务)
kd search "ENG_BOM" --product 0 --size 5            # 结果带 products=["企业版/标准版"]
kd ask "旗舰版BOM维护表单标识" --kw "BOM维护 表单标识" --product 93 --topk 1
#   → routes 无 productIds,召回 detail.products=['企业版','标准版',...] ← --kw 丢过滤实锤
# 缺陷 2(上游,节选)
curl -s -o /dev/null -A "$UA" -w "%{http_code} %{redirect_url}" https://vip.kingdee.com/question/799346568250934528   # 302→login-url
curl -s -o /dev/null -A "$UA" -w "%{http_code} %{redirect_url}" https://vip.kingdee.com/link/s/clzL8                  # 302→/searchchats/{id}
curl -s -A "$UA" https://vip.kingdee.com/api/questions/799346568250934528                                             # 200 全文 JSON
curl -s -A "$UA" https://vip.kingdee.com/aisapi/ai-search/sharing-chats/884194634405046272                            # 200
curl -s -o /dev/null -A "$UA" -w "%{http_code}" https://help.open.kingdee.com/dokuwiki/doku.php                       # 200
```

**其他:**

- 会话收尾文档:`~/.lingeebuild/corpus/usage/2026-09-06-bom维护-开发平台表单.md`(事故记录与修正结论)
- 既有调研报告格式参照:`docs/research/2026-09-06-grep-vs-rag.md`
