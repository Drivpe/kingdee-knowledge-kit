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
- **保持人类调用频率**,勿高频轰炸;全链路零账号/零 cookie/零点数是红线;
- 本仓库不含任何凭据;完整安全声明见[文末](#安全)。仅供个人学习使用。

## 为什么选 kd?

- **为 agent 原生设计** —— 默认 JSON、stdout=数据/stderr=进度、错误即数据(`hint` 带修复指引)、退出码 0/1/2、永不交互;`kd --help` 与 `kd health` 自曝命令面与内核状态,agent 不读长文档即会用
- **免费且无门槛** —— 官方 AI 问答要登录+点数,kd 全链路匿名,零账号零点数
- **覆盖面完整** —— 官方文档、社区问答帖(含采纳回答与追问链)、社区文章,三种来源一次打通
- **不被模型绑死** —— 套件不持有任何模型通道,零模型依赖;清单与全文交给调用方 agent,用哪个模型、要不要开子代理,你自己定
- **排序交给上游** —— 每一路检索拿回的都是金蝶云社区官方综合排序的结果,内核**只去重、不做任何算法评分**(没有 RRF、没有融合分、没有 hitRoutes 加权);清单顺序本身就是相关度顺序,读哪几篇由 LLM 判断
- **回答有规范** —— [ANSWER-SPEC](docs/ANSWER-SPEC.md) 对齐官方 AI 样例:原因分析→分步方案→操作边界、表格、`knowledge`/`article` 引用做成可点击链接(`question` 只给标题与出处)、资料未覆盖诚实声明;不达标时如实说明,不硬凑
- **零依赖部署** —— 内核与 CLI 均为 Python 纯标准库,无第三方依赖,`pipx install` 一条命令即可(无 Python 环境时用一键脚本兜底)

## 功能

| 域 | 能力 |
|---|---|
| 🔍 检索 | 三种实体(官方文档/社区问答/社区文章)一次全返回,按产品线(旗舰版/苍穹/企业版/星空二开)路由,由调用层判定并显式传入;多路拆词 ≤7 路,每路各发一次上游检索后**只去重**,顺序 = (路序, 路内上游名次) |
| 📖 全文 | 知识库文档全文、问答帖全文(问题+全部回答+追问链,采纳优先)、社区文章全文 |
| 📋 清单 | `kd search`(**唯一检索入口**):稀有数字 token 路 + 原句路 + 症状词路 + 字段/实体名词路 + 产品词路,只出**帖级**标题清单(带 `hitRoutes`/`routes[]` 供你判断),正文由 `kd read` 按需取 |

> **v6.5 全面修复**(2026-09-28,ADR-0015)——链接口径按 kind 分档: **`knowledge`/`article` 引用给链接、`question` 只给标题与出处**;内核新增**稀有数字 token 路**(错误码/单号自动抢占第 1 路,默认路径不再劣于手工拆词);`read` 的 `budget` 参数与 `search` 统一;字段集的回归检查由"自己比自己"改为**冻结基线 + 真实输出 + 注入自检**;`docs/adr/0015-挑选判据归属.md` 钉死「按标题挑」的边界(判据进文档、内核零感知)。
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

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.ps1 | iex
```

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.sh | bash
```

装什么:①kd CLI(`~/.kingdee-kit/bin` 或 `%USERPROFILE%\.kingdee-kit\bin`,加入 PATH)
②技能(`~/.agents/skills/kingdee-knowledge`) ③自动跑 `tests/kd_regression.py` 离线组 35 项,全绿才放行(`kd health` 冒烟验证同一批闸门)。
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
| `kd search "关键词" [--kw 词]… [--product 87] [--type question] [--max-routes 7] [--global]` | 检索:**只出帖级标题清单**(官方文档/社区问答/文章三种实体),按 (路序, 路内上游名次) 排列 |
| `kd read <id> [--kind knowledge\|question\|article]` | 读全文:`--kind` 照抄 search 结果的 `type`,零翻译 |
| `kd health` | 内核自检(库模式:无服务、无端口、无 HTTP) |

`--product` 语义:93=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、2=星空侧二开问答专区、0=不过滤(显式指定才生效)。

产品线**由调用层判定**:你可以显式传 `--product N`,内核照用,不会因为问句里出现「苍穹」等字样而改写它
(内核**不做任何字面推导**;只有你不传时,才落到默认 93)。判定口径见
[SKILL.md](skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md) 的「产品线语义识别」节。

`--kw` 是**给 LLM 用的拆词入口**:可重复,每个词一路,**替代内核自动拆解**(原句路仍会发);只给 `--kw` 时不必再给 text。

> **没有 `kd ask`。** 单入口定案(ADR-0013):`ask` 及其专属件已整体删除,调用 `kd ask` 会得到 argparse 退出码 2。
> **排序不是内核算的**:每一路都是上游综合排序的产物,内核只去重;`hitRoutes`/`routes[]` 是纯信息字段,不参与排序。
> 拿到清单后自己挑、`kd read` 取全文,再由你(agent)按 ANSWER-SPEC 合成。

## 进阶

### JSON 契约(AI-first 七原则)

- 默认输出 JSON;**stdout=数据,stderr=进度**(无 ANSI 色码)
- 错误是 JSON `{"code","message","hint","example"}`,`hint` 给修复指引(含可执行下一步)
- 退出码:`0` 成功 / `1` 上游或内部错误 / `2` 用法错误
- 永不交互;两级 `--help` 带示例;大输出标 `truncated` 并指路下一步
- agent 自发现:`kd --help` 带三条子命令与示例;`kd health` 回吐内核版本、公开面、路由规则路径与预算上限

### `search` 返回字段

| 字段 | 说明 |
|---|---|
| `text` / `keywords` | 回显;仅给 `--kw` 时 `text` 为 `null` |
| `total` | 各路 `totalElements` 的最大值 |
| `queries[]` | 去重后**实际**发出的检索词,顺序即路序 |
| `routesPlanned` | 计划路数(去重前,受 `--max-routes` 截断后) |
| `routesDegraded` | **路数塌缩**:去重后实际路数 < 计划路数时 `true` |
| `effectiveProductId` | 本次**实际生效**的产品过滤(整数,与 `--product` 同值域;不传即默认 93)。内核不做字面推导,故它**恒等于你传入的值** |
| `results[]` | **帖级**清单条目:`type`/`id`/`title`/`url`/`hitRoutes`/`routes[]`/`snippet`/`products`/`adopted`/`answersCount`/`comments`/`supports`/`questionBody` |
| `routeErrors[]` | 失败路(`{route,kind,terms,error,code,message}`)——用于区分"被上游拒绝"与"官方没这类文档" |
| `budget_exhausted` | 上游请求硬上限耗尽,清单不完整 |
| `scanNote` | 人读诊断串(含"路数塌缩:N→M 路") |
| `stats` | `{upstreamCalls, elapsedMs}` |

`results[]` 里**没有** `contentText`——要全文必须 `kd read`。也**没有任何 score 字段**,
没有 `page`/`pageSize`/`totalPages`(清单分页已于 v6.4 删除),没有 `questionId`
(帖级化后条目 `id` 就是帖子号)。

字段集的**单一来源**是包内 `src/kd/contract.json`:代码读它拼返回体,回归用例从它派生断言。
改字段只改这一处,不用同步六份抄本。

### 环境变量

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `KSEARCH_SEARCH_BUDGET` | 取包内 `kd/query_routes.json`(默认 24) | 单次 `search` 上游请求硬上限 |

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

判定依据全部来自 `search` 清单的客观信号:`routeErrors[]`(部分路失败)、`budget_exhausted`(预算耗尽)、
`routesDegraded`(路数塌缩)、以及标题与问句的匹配度。**不用数值分数**——LLM 自报概率无校准,
且与上游信号不同量纲,反而污染判断。

### 回答规范

所有合成回答遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md):
三段式结构(原因分析→解决方案→操作边界)、表格、**`knowledge`/`article` 引用贴可点击链接**(`[标题](<原始 url>)`)、**`question` 只给标题与出处**(匿名 9/9 不可点,详见 [contract.json](src/kd/contract.json) 的 `linkPolicy` —— 链接政策的唯一真源)、无自用编号、不要求文末独立的来源列表、资料未覆盖诚实声明。

### 回归

改内核/CLI 后:`python3 tests/kd_regression.py`(离线 35 项,不联网、不需要任何环境变量);
联网用例加 `--online`(另 15 项,真实上游、保持人类频率)。
离线组内含原先由 `scripts/check_core_surface.py` 承担的三条判据(版本号单一真源 /
`health` 内部件依赖可解析 / kind 集合三源一致)——该脚本已于 v6.4 整体删除。
检索侧改动另需手工用例验收(原 `run_eval` 评测体系已随去服务化删除)。

## 安全

- **本仓库不含任何凭据**。cookie、账号 token、API key、日志、含本机路径的笔记一律不入库,`.gitignore` 已按模式拦截——推送前 `git status` 再核对一遍
- 上游为金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;保持人类调用频率,勿高频轰炸
- 本套件不含任何模型密钥——它不调模型(ADR-0008)
- 公开仓库等于公开接口细节,建议私有库,或接受"仅供个人学习使用"的公开声明

## License

MIT
