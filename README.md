# kingdee-knowledge-kit

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![AI-first](https://img.shields.io/badge/design-AI--first-8A2BE2.svg)

**kd —— agent 查金蝶官方知识的金手指:一个可 import 的检索内核 + 一个 AI 友好的 CLI + 一个跨客户端技能。**
逆向自金蝶云社区公开检索后端,**零账号、零 cookie、零官方点数、零模型依赖**——检索、全文、问答帖全匿名;
本套件**只出清单、不合成回答**(ADR-0008):`kd search` 出标题清单 → 调用方 agent 自己挑 → `kd read` 取全文 → 按规范合成带引用回答。

[为什么选 kd](#为什么选-kd) · [安装](#安装) · [命令](#kd-命令3-条) · [进阶](#进阶) · [使用前必读](#️-使用前必读) · [安全](#安全)

> **Note for AI assistants**:① 先跑 `kd --help` 看三条子命令,再跑 `kd health` 确认内核可用;
> ② stdout 永远是 JSON 数据,进度在 stderr,错误是带 `hint` 的 JSON;③ 回答格式遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md)。

## ⚠️ 使用前必读

- 上游是金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;
- **保持人类调用频率**,勿高频轰炸;全链路零账号/零 cookie/零点数是红线。
  **频率上限的唯一真源是 `src/kd/contract.json` 的 `limits.rate`**(当前 `burst 7 / rps 5` + 抖动,
  即一次操作内最多 7 个请求同时在飞、长期速率 5 请求/秒)。内核由 `_RateLimiter` 强制执行,
  调用方无需自行节流;**勿绕过内核直连上游**;
- 本仓库不含任何凭据;完整安全声明见[文末](#安全)。仅供个人学习使用。

## 为什么选 kd?

- **为 agent 原生设计** —— 默认 JSON、stdout=数据/stderr=进度、错误即数据(`hint` 带修复指引)、退出码 0/1/2、永不交互;`kd --help` 与 `kd health` 自曝命令面与内核状态,agent 不读长文档即会用
- **免费且无门槛** —— 官方 AI 问答要登录+点数,kd 全链路匿名,零账号零点数
- **覆盖面完整** —— 官方文档、社区问答帖(含采纳回答与追问链)、社区文章,三种来源一次打通
- **不被模型绑死** —— 套件不持有任何模型通道,零模型依赖;清单与全文交给调用方 agent,用哪个模型、要不要开子代理,你自己定
- **排序交给上游** —— 每一路检索拿回的都是金蝶云社区官方综合排序的结果,内核**只去重、不做任何算法评分**(没有 RRF、没有融合分、没有 hitRoutes 加权);清单顺序本身就是相关度顺序,读哪几篇由 LLM 判断
- **回答有规范** —— [ANSWER-SPEC](docs/ANSWER-SPEC.md) 对齐官方 AI 样例:原因分析→分步方案→操作边界、表格、`knowledge`/`article`/`question` 引用做成可点击链接(问答用长形式 `/questions/<帖子号>/answers/<回答号>`,2026-09-29 改判)、资料未覆盖诚实声明;不达标时如实说明,不硬凑
- **零依赖部署** —— 内核与 CLI 均为 Python 纯标准库,无第三方依赖,`pipx install` 一条命令即可(无 Python 环境时用一键脚本兜底)

## 功能

| 域 | 能力 |
|---|---|
| 🔍 检索 | 三类已知实体(官方文档/社区问答/社区文章)一次全返回(罕见类型归入 `other` 档、默认隐藏),按产品线(旗舰版/苍穹/企业版/星空二开)路由,由调用层判定并显式传入;多路关键词 ≤7 路(超限按你给词的顺序取前 N 并**写明丢了几条**),每路各发一次上游检索后**只去重**,顺序 = (你给词的顺序, 词内上游名次) |
| 📖 全文 | 知识库文档全文、问答帖全文(问题+全部回答+追问链,采纳优先)、社区文章全文 |
| 📋 清单 | `kd search`(**唯一检索入口**):你按拆词规范拆好的关键词,每个 `--kw` 一路,**内核原样按序发送**;只出**帖级**标题清单(带 `hitRoutes`/`routes[]` 供你判断),正文由 `kd read` 按需取 |

> **v6.6 检索词生成权移交**(2026-09-28,ADR-0016,**破坏性变更**)——内核**不再生成任何检索词**:
> 规则拆词器(稀有 token 路 / 原句路 / 症状词路 / 字段实体名词路 / 产品词路)整体删除,
> `query_routes.json` 文件删除(路数上限与限速档并入 `contract.json` 的 `limits` 段);
> **位置参数与 `--type`/`--max-routes`/`--budget`/`--chunk` 全部删除**,`--kw` 成为唯一入口;
> **预算机制与跨页扫描删除**(每路恒 1 次请求);`--product` 三态收两态(`None` 与不传同义,
> 不过滤只由显式 `0` 表达);清单**字段集按类型分四份**(第四份 `other` 为 v6.7 新增;类型不适用的键**不出现**,而非填 `null`);
> `read(question)` 的截断由布尔改为**原因枚举**(`answer_limit` / `upstream_error`);
> 新增顶层 `keywordsDropped`(超限丢了几条词)。
> ⚠️ **迁移**:`kd search "整句"` → `kd search --kw "词1" --kw "词2"`(拆词规范见 `SKILL.md`);
> `--max-routes 1` → 只给一个 `--kw`;`--product 0` 仍是"不过滤",`product_id=None` 改传 `0`。
> **v6.5 全面修复**(2026-09-28,ADR-0015)——链接口径按 kind 分档: **`knowledge`/`article` 引用给链接、`question` 当时判为不给**;内核新增**稀有数字 token 路**(错误码/单号自动抢占第 1 路,默认路径不再劣于手工拆词);`read` 的 `budget` 参数与 `search` 统一;字段集的回归检查由"自己比自己"改为**冻结基线 + 真实输出 + 注入自检**;`docs/adr/0015-挑选判据归属.md` 钉死「按标题挑」的边界(判据进文档、内核零感知)。
> **v6.4 契约重构**(2026-09-27,ADR-0014)——清单粒度由**回答级改为帖子级**:
> 同一帖的多条回答在上游是多个条目,清单里**合并为一条**,条目 `id` 就是**帖子号**
> (`questionId` 字段整体删除,双 id 空间消失);对外问答类型名由 `answer` 改为
> **`question`**(上游协议里仍是 `Answer`,映射只在 `_norm_item` 一处);
> **产品线字面推导整体删除**(问句里的「苍穹」不再改写你显式传的值);
> **清单分页删除**(`--page`/`--size` 与 `page`/`page_size` 移除,清单一次给全);
> 字段集收进包内 `contract.json`(单一来源)。
> 字段增减:顶层 16 → 13(砍 `page`/`pageSize`/`totalPages`)、条目 16 → 13
> (砍 `questionId`/`views`/`updatedAt`)。
> **v6.3**:去服务化(ADR-0011)——HTTP 服务与十个端点、`kd share` / `kd manifest` 命令、本地落盘缓存
> (`corpus/`、`~/.lingeebuild/landing`、`data/ksearch.db`)、`semantic_rerank` 与 `run_eval` 评测体系全部删除;
> 检索内核改为可 import 的库(`kd.core` 公开面 = `search`/`read` + 三异常类),CLI 只剩 `search`/`read`/`health` 三条。
> v6.2:合成权移交调用方(ADR-0008)——`kd ai` 删除、`KAI_*` 环境变量废除,套件零模型依赖;原句作为独立检索路参与 RRF(ADR-0009);召回置信度用三档枚举(ADR-0010)。
> v6.0:彻底在线(ADR-0005)——查询时多路检索为主轴,corpus 预爬语料废除;
> v5.0:grep 语料路线定案(corpus+rg 接管检索角色,向量降级待墙),见 `docs/adr/0004`;
> v4.0 评测结论:信号重排、同义词变体被评测否决(recall 反降/无增益),默认关或移除,详见 `docs/eval-report-v4.md` 与 `docs/adr/0003`。

## 安装

### ① pipx(推荐,有 Python 3.8+ 的机器)

```bash
pipx install kingdee-knowledge-kit && kd health
```

由 `pyproject.toml` 的 `[project.scripts]` 提供 `kd`,升级/卸载交给 pipx 管。零第三方依赖,装完即可用。

### ② 一键脚本(兜底:无 Python 环境或统一装机)

脚本自身不含源码 —— 库体(`src/kd`)、技能(`skills/`)、验证闸门(`tests/kd_regression.py`)
都从**仓库检出**里取,所以它的稳态用法是**先克隆再原地运行**:

Linux / macOS:

```bash
git clone --depth 1 https://github.com/Drivpe/kingdee-knowledge-kit.git
cd kingdee-knowledge-kit && ./install.sh
```

Windows PowerShell(同样在检出目录里):

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

也可以直接管道下发:管道形式下脚本没有"脚本自身所在目录",它会先把仓库**浅克隆到临时目录**
再继续,装完即删。这条路多两个前提 ——

```powershell
# Windows PowerShell
irm https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.ps1 | iex
```

```bash
# Linux / macOS
curl -fsSL https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.sh | bash
```

- 需要 `git` 且能访问 GitHub(拉不到就回落成下面的清晰报错,不会半途失败);
- **管道下发的脚本 = 远程那一版**:远程还没同步时,你拿到的是旧脚本,探测与报错逻辑都不参与,
  所以**远程落后时请用上面的 clone 形式**。

两种形式都取不到检出目录时,脚本**不半途失败**:它打印上面的 clone 用法并以非 0 退出
(不会出现 `cp: cannot stat '…/src/kd'` 这种既看不懂也不知道怎么办的报错)。

装什么:①kd CLI(`~/.kingdee-kit/bin` 或 `%USERPROFILE%\.kingdee-kit\bin`,加入 PATH)
②技能(`~/.agents/skills/kingdee-knowledge`) ③自动跑 `tests/kd_regression.py` 的离线组,**全绿才放行**
(该组条数以脚本自身输出为准;`kd health` 冒烟验证同一批闸门)。
开关:`--root DIR` / `--no-path` / `--no-skills` / `--no-verify`(PowerShell 侧为 `-InstallRoot -NoPath -NoSkills -NoVerify`)。

> 内核是**库**,不是服务:安装后不常驻进程、不开端口、不写仓外数据。

### ③ AI Agent 三步接入(已有 kd 的机器)

```text
1. 跑 kd health —— 确认内核可用;kd --help 看三条子命令与示例
2. 有技能的 agent:装下面技能后问金蝶问题即自动走 kd;没有技能:把 --help 输出读进上下文
3. 流程固定三步:`kd search` 出清单 → 自己挑 → `kd read` 取全文 → 按 docs/ANSWER-SPEC.md 的格式合成(带引用,资料未覆盖要声明)
```

只要技能(机器上已有 kd):把 `skills/kingdee-knowledge/skills/kingdee-knowledge/` 复制到
`~/.agents/skills/`;或 `npx skills add Drivpe/kingdee-knowledge-kit`;或 ZCode 插件面板
Settings → Plugins → Discover → 添加本仓库 GitHub 地址 → Get。

### ④ 开发者

```bash
git clone https://github.com/Drivpe/kingdee-knowledge-kit.git && cd kingdee-knowledge-kit
powershell -File install.ps1 -InstallRoot D:\kit -NoPath -NoSkills   # Windows 自定义
bash install.sh --root ~/kit --no-path --no-skills                   # *nix 自定义
```

## kd 命令(3 条)

| 命令 | 作用 |
|---|---|
| `kd search --kw 词 [--kw 词]… [--product 87] [--global] [--include-other]` | 检索:**只出帖级标题清单**(官方文档/社区问答/文章三类实体;罕见类型归入 `other` 档且**默认隐藏**),按 (你给词的顺序, 词内上游名次) 排列 |
| `kd read <id> [--kind knowledge\|question\|article]` | 读全文:`--kind` 照抄 search 结果的 `type`,零翻译(注:`other` 档是合法 type 但**没有全文端点**,`read` 会明确说明) |
| `kd health` | 内核自检(库模式:无服务、无端口、无 HTTP) |

`--product` 语义:93=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、2=星空侧二开问答专区、0=不过滤(显式指定才生效)。

产品线**由调用层判定**:你可以显式传 `--product N`,内核照用,不会因为问句里出现「苍穹」等字样而改写它
(内核**不做任何字面推导**;只有你不传时,才落到默认 93)。判定口径见
[SKILL.md](skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md) 的「产品线语义识别」节。

`--kw` 是**唯一入口**(v6.6 起位置参数已删):可重复,每个词一路。内核**不拆词、不改序、不扩充**——怎么拆由你按 `SKILL.md` 的拆词规范决定,给词的顺序就是召回顺序。

> **没有 `kd ask`。** 单入口定案(ADR-0013):`ask` 及其专属件已整体删除,调用 `kd ask` 会得到 argparse 退出码 2。
> **排序不是内核算的**:每一路都是上游综合排序的产物,内核只去重;`hitRoutes`/`routes[]` 是纯信息字段,不参与排序。
> 拿到清单后自己挑、`kd read` 取全文,再由你(agent)按 ANSWER-SPEC 合成。

## 进阶

### JSON 契约(AI-first 七原则)

- 默认输出 JSON;**stdout=数据,stderr=进度**(无 ANSI 色码)
- 错误是 JSON `{"code","message","hint","example"}`,`hint` 给修复指引(含可执行下一步)
- 退出码:`0` 成功 / `1` 上游或内部错误 / `2` 用法错误
- 永不交互;两级 `--help` 带示例;大输出标 `truncated` 并指路下一步
- agent 自发现:`kd --help` 带三条子命令与示例;`kd health` 回吐内核版本、公开面、契约声明路径与收词上限

### `search` 返回字段

| 字段 | 说明 |
|---|---|
| `keywords` | 回显你给的词列表(v6.6 起顶层**不再有 `text`**;v6.9 起顶层**不再有 `total`**) |
| `queries[]` | 去重后**实际**发出的检索词,顺序即路序 |
| `routesPlanned` | 计划路数(去重前,受声明的收词上限截断后) |
| `effectiveProductId` | 本次**实际生效**的产品过滤(整数,与 `--product` 同值域;不传即默认 93)。内核不做字面推导,故它**恒等于你传入的值** |
| `results[]` | **帖级**清单条目,**字段集按 `type` 分四份**:公共 `type`/`id`/`title`/`url`/`snippet`/`products`/`comments`/`hitRoutes`/`routes[]`;`question` 另加 `adopted`/`answersCount`/`questionBody`;`article` 另加 `supports`;`other` 另加 `upstreamType`/`resourceType`(罕见类型默认隐藏,见 `otherSkipped` 与 `--include-other`)。类型不适用的键**直接不出现**(不是 `null`) |
| `routeErrors[]` | 失败路(`{route,kind,terms,error,code,message}`)——用于区分"被上游拒绝"与"官方没这类文档" |
| `keywordsDropped` | 因超出收词上限而未发出的词数(0 = 没丢);>0 即召回按定义不完整 |
| `otherSkipped` | 被**隐藏**掉的罕见类型条数(`other` 档默认隐藏;0 = 没跳过或已用 `--include-other` 打开)。⚠️ 隐藏**必须可见**——它让"清单比实际召回少了一截"的差额永远有解释 |
| `scanNote` | 人读诊断串(含罕见类型跳过数、超限丢词与"丢弃空白词 N 条"说明) |
| `contractCfgLoaded` | 包内 `contract.json` **是否真的读到**。`false` ⇒ 链接政策已回落"全部不给链接",**所有 `url` 是 `null`**——那是"没读到声明",不是"官方这些条目没链接" |
| `stats` | `{upstreamCalls, elapsedMs}` |

`results[]` 里**没有** `contentText`——要全文必须 `kd read`。也**没有任何 score 字段**,
没有 `page`/`pageSize`/`totalPages`(清单分页已于 v6.4 删除),没有 `questionId`
(帖级化后条目 `id` 就是帖子号),顶层没有 `text` 与 `budget_exhausted`(v6.6 删除)、
没有 `total`(v6.9 删除:**破坏性变更**,理由见 [contract.json](src/kd/contract.json) 的
`search.note_totalRemoved`)、
没有 `routesDegraded`(2026-09-29 删除:它报得自相矛盾,用户口径为"重复的不提示")。

⚠️ **类型不适用的键不出现 ≠ 值为 `null`**(v6.6):前者是"结构性不适用"(知识文档永远
没有回答数),后者是"上游没给值"。混成一个会让调用方分不清,故 `adopted` 只出现在
`question` 条目上、`supports` 只出现在 `article` 条目上。

字段集的**单一来源**是包内 `src/kd/contract.json`:代码读它拼返回体,回归用例从它派生断言。
改字段只改这一处,不用同步六份抄本。

### 环境变量

| 环境变量 | 默认 | 说明 |
|---|---|---|
| ~~`KSEARCH_SEARCH_BUDGET`~~ | **已删除**(v6.6):每路恒 1 次请求,预算机制整体移除 | —— |

> `KSEARCH_ASK_BUDGET` 已随 `ask` 删除(ADR-0013)。
> `KAI_BASE` / `KAI_MODEL` 已废除(ADR-0008)——本套件不再持有模型通道。
> `KSEARCH_URL` 已废除(去服务化)——内核进程内直连,无 HTTP、无端口。
> `KSEARCH_INDEX` 亦已失效:本地 sqlite 上游缓存随去服务化整体删除,该变量不再改变任何行为。

### 合成与置信度(调用方职责)

`kd search` 出的是**清单**,`kd read` 取回的是**全文**;两者都不是成品答案。合成方自己挑、自己读、
自己产出「答案 + 结构化引用」,并对召回是否足以作答给出**三档判定**(ADR-0010):

| 档位 | 判据 |
|---|---|
| `answerable` | 读到的全文里有段落直接说明了机制/字段/步骤 |
| `partial` | 有相关材料,但未覆盖用户问的那个点 |
| `uncovered` | 清单与问题不匹配,或检索本身未命中 |

判定依据全部来自 `search` 清单的客观信号:`routeErrors[]`(部分路失败)、`keywordsDropped`(有词被超限截掉)、
以及标题与问句的匹配度。**不用数值分数**——LLM 自报概率无校准,
且与上游信号不同量纲,反而污染判断。

### 回答规范

所有合成回答遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md):
三段式结构(原因分析→解决方案→操作边界)、表格、**`knowledge`/`article` 引用贴可点击链接**(`[标题](<原始 url>)`)、**`question` 给带回答号的长形式链接**(2026-09-29 改判:短形式恒不可点,长形式实测 22/22 可点;详见 [contract.json](src/kd/contract.json) 的 `linkPolicy` —— 链接政策的唯一真源)、无自用编号、不要求文末独立的来源列表、资料未覆盖诚实声明。

### 回归

改内核/CLI 后:`python3 tests/kd_regression.py`(离线组,不联网、不需要任何环境变量;该组条数以脚本自身输出为准);
联网用例加 `--online`(另跑一组真实上游用例,数量同样以脚本输出为准;保持人类频率)。
离线组内含原先由 `scripts/check_core_surface.py` 承担的三条判据(版本号单一真源 /
`health` 内部件依赖可解析 / kind 集合三源一致)——该脚本已于 v6.4 整体删除。
检索侧改动另需手工用例验收(原 `run_eval` 评测体系已随去服务化删除)。

## 安全

- **本仓库不含任何凭据**。cookie、账号 token、API key、日志、含本机路径的笔记一律不入库,`.gitignore` 已按模式拦截——推送前 `git status` 再核对一遍
- 上游为金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;保持人类调用频率,勿高频轰炸(频率上限与唯一真源见上文「使用前必读」,由内核 `_RateLimiter` 强制)
- 本套件不含任何模型密钥——它不调模型(ADR-0008)
- 公开仓库等于公开接口细节,建议私有库,或接受"仅供个人学习使用"的公开声明

## License

MIT
