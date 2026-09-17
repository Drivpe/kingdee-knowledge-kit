# kingdee-knowledge-kit

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![AI-first](https://img.shields.io/badge/design-AI--first-8A2BE2.svg)

**kd —— agent 查金蝶官方知识的金手指:一个可 import 的检索内核 + 一个 AI 友好的 CLI + 一个跨客户端技能。**
逆向自金蝶云社区公开检索后端,**零账号、零 cookie、零官方点数、零模型依赖**——检索、全文、问答帖全匿名;
本套件**只产资料包、不合成回答**(ADR-0008):调用方 agent 拿 `kd ask` 的资料包,按规范自己合成带引用回答。

[为什么选 kd](#为什么选-kd) · [安装](#安装) · [命令](#kd-命令4-个) · [进阶](#进阶) · [使用前必读](#️-使用前必读) · [安全](#安全)

> **Note for AI assistants**:① 先跑 `kd --help` 看四条子命令,再跑 `kd health` 确认内核可用;
> ② stdout 永远是 JSON 数据,进度在 stderr,错误是带 `hint` 的 JSON;③ 回答格式遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md)。

## ⚠️ 使用前必读

- 上游是金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;
- **保持人类调用频率**,勿高频轰炸;全链路零账号/零 cookie/零点数是红线;
- 本仓库不含任何凭据;完整安全声明见[文末](#安全)。仅供个人学习使用。

## 为什么选 kd?

- **为 agent 原生设计** —— 默认 JSON、stdout=数据/stderr=进度、错误即数据(`hint` 带修复指引)、退出码 0/1/2、永不交互;`kd --help` 与 `kd health` 自曝命令面与内核状态,agent 不读长文档即会用
- **免费且无门槛** —— 官方 AI 问答要登录+点数,kd 全链路匿名,零账号零点数
- **覆盖面完整** —— 官方文档、社区问答帖(含采纳回答与追问链)、社区文章,三种来源一次打通
- **不被模型绑死** —— 套件不持有任何模型通道,零模型依赖;资料包交给调用方 agent,用哪个模型、要不要开子代理,你自己定
- **回答有规范** —— [ANSWER-SPEC](docs/ANSWER-SPEC.md) 对齐官方 AI 样例:原因分析→分步方案→操作边界、表格、[n] 编号引用、资料未覆盖诚实声明;不达标时如实说明,不硬凑
- **零依赖部署** —— 内核与 CLI 均为 Python 纯标准库,无第三方依赖,`pipx install` 一条命令即可(无 Python 环境时用一键脚本兜底)

## 功能

| 域 | 能力 |
|---|---|
| 🔍 检索 | 三种实体(官方文档/社区问答/社区文章)一次全返回,按产品线(旗舰版/苍穹/企业版)路由 |
| 📖 全文 | 知识库文档全文、问答帖全文(问题+全部回答+追问链,采纳优先)、社区文章全文 |
| 📦 资料包 | `kd ask`(**唯一常规入口**:原句路+多路关键词拆解 ≤7 路 RRF+上游预算)检索+深读 topK 全文一站带回,附 **chunk 命中段落**与 **召回信号摘要**(`synthesisBrief`,供合成方判定置信度),交调用方 agent 合成带引用回答 |

> v6.3:去服务化(ADR-0011)——HTTP 服务与十个端点、`kd share` / `kd manifest` 命令、本地落盘缓存
> (`corpus/`、`~/.lingeebuild/landing`、`data/ksearch.db`)、`semantic_rerank` 与 `run_eval` 评测体系全部删除;
> 检索内核改为可 import 的库(`kd.core` 公开面 = `ask`/`search`/`read` + 三异常类),CLI 只剩 `search`/`read`/`ask`/`health` 四条。
> v6.2:合成权移交调用方(ADR-0008)——`kd ai` 删除、`KAI_*` 环境变量废除,套件零模型依赖;原句作为独立检索路参与 RRF(ADR-0009);召回置信度用三档枚举(ADR-0010)。
> v6.0:彻底在线(ADR-0005)——查询时多路检索为主轴,corpus 预爬语料废除;`kd ask` 唯一常规入口(search/read 降为手动细粒度调试命令);
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
②技能(`~/.agents/skills/kingdee-knowledge`) ③自动跑 `tests/kd_regression.py` 离线组 10 项,全绿才放行(`kd health` 冒烟验证同一批闸门)。
开关:`--root DIR` / `--no-path` / `--no-skills` / `--no-verify`(PowerShell 侧为 `-InstallRoot -NoPath -NoSkills -NoVerify`)。

> 内核是**库**,不是服务:安装后不常驻进程、不开端口、不写仓外数据。

### ③ AI Agent 三步接入(已有 kd 的机器)

```text
1. 跑 kd health —— 确认内核可用;kd --help 看四条子命令与示例
2. 有技能的 agent:装下面技能后问金蝶问题即自动走 kd;没有技能:把 --help 输出读进上下文
3. 回答按 docs/ANSWER-SPEC.md 的格式(带 [n] 引用,资料未覆盖要声明)
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

## kd 命令(4 条)

| 命令 | 作用 |
|---|---|
| `kd search "关键词" [--product 93] [--type answer] [--size 10]` | 检索:官方文档/社区问答/文章三种实体全返回 |
| `kd read <id> [--kind knowledge\|answer\|article]` | 读全文:`--kind` 照抄 search 结果的 `type`,零翻译 |
| `kd ask "问题" [--topk 4]` / `--kw "词1" --kw "词2"` | 一站式资料包(原句路+多路拆解检索+深读 topK 全文+`synthesisBrief`),交给调用方 agent 合成 |
| `kd health` | 内核自检(库模式:无服务、无端口、无 HTTP) |

`--product` 语义:93=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、0=不过滤(等价省略参数)。

> **没有 `kd ai`。** 合成权在调用方(ADR-0008):本套件零模型依赖,只产资料包。拿到资料包后由 agent(建议开子代理,避免长文档污染主上下文)按 ANSWER-SPEC 合成,提示词模板见技能 `SKILL.md`。

## 进阶

### JSON 契约(AI-first 七原则)

- 默认输出 JSON;**stdout=数据,stderr=进度**(无 ANSI 色码)
- 错误是 JSON `{"code","message","hint","example"}`,`hint` 给修复指引(含可执行下一步)
- 退出码:`0` 成功 / `1` 上游或内部错误 / `2` 用法错误
- 永不交互;两级 `--help` 带示例;大输出标 `truncated` 并指路下一步
- agent 自发现:`kd --help` 带四条子命令与示例;`kd health` 回吐内核版本、公开面、路由规则路径与预算上限

### 环境变量

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `KSEARCH_ASK_BUDGET` | 取包内 `kd/query_routes.json`(默认 64) | 单次 ask 上游请求硬上限 |

> `KAI_BASE` / `KAI_MODEL` 已废除(ADR-0008)——本套件不再持有模型通道。
> `KSEARCH_URL` 已废除(去服务化)——内核进程内直连,无 HTTP、无端口。
> `KSEARCH_INDEX` 亦已失效:本地 sqlite 上游缓存随去服务化整体删除,该变量不再改变任何行为。

### 合成与置信度(调用方职责)

`kd ask` 返回的资料包是**半成品**:`sources`(含 topK 全文与 chunk 片段)、`routes`(多路拆解明细)、
`budget`(上游消耗)、`synthesisBrief`(召回信号摘要:`topScores`/`routeKinds`/`budgetExhausted`)。

合成方据此产出「答案 + 结构化引用」,并对召回是否足以作答给出**三档判定**(ADR-0010):

| 档位 | 判据 |
|---|---|
| `answerable` | 召回文档中有段落直接说明了机制/字段/步骤 |
| `partial` | 有相关材料,但未覆盖用户问的那个点 |
| `uncovered` | 召回内容与问题不匹配,或检索本身未命中 |

**不用数值分数**——LLM 自报概率无校准,且与 `topScores` 这类客观量不同量纲,反而污染判断。

### 回答规范

所有合成回答遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md):
三段式结构(原因分析→解决方案→操作边界)、表格、`[n]` 编号引用+文末来源列表、资料未覆盖诚实声明。

## 回归

改内核/CLI 后:`python3 tests/kd_regression.py`(离线组 10 项,不联网、不需要任何环境变量)。
联网用例加 `--online`。公开面守卫另跑 `python3 scripts/check_core_surface.py`(应为 PASS)。
检索侧改动另需手工用例验收(原 `run_eval` 评测体系已随去服务化删除)。

## 安全

- **本仓库不含任何凭据**。cookie、账号 token、API key、日志、含本机路径的笔记一律不入库,`.gitignore` 已按模式拦截——推送前 `git status` 再核对一遍
- 上游为金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;保持人类调用频率,勿高频轰炸
- 本套件不含任何模型密钥——它不调模型(ADR-0008)
- 公开仓库等于公开接口细节,建议私有库,或接受"仅供个人学习使用"的公开声明

## License

MIT
