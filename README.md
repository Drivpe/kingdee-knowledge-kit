# kingdee-knowledge-kit

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![AI-first](https://img.shields.io/badge/design-AI--first-8A2BE2.svg)

**kd —— agent 查金蝶官方知识的金手指:一个匿名检索服务 + 一个 AI 友好的 CLI + 一个跨客户端技能。**
逆向自金蝶云社区公开检索后端,**零账号、零 cookie、零官方点数**——检索、全文、问答帖、官方分享对话全匿名;
回答由你的 AI 合成(`kd ai` 走你自己的模型通道,或调用方 AI 拿 `kd ask` 资料包自己写)。

[为什么选 kd](#为什么选-kd) · [安装](#安装) · [命令](#kd-命令7-个) · [进阶](#进阶) · [使用前必读](#️-使用前必读) · [安全](#安全)

> **Note for AI assistants**:① 先跑 `kd manifest` 拿机器可读能力清单,不需要读本页全文;
> ② stdout 永远是 JSON 数据,进度在 stderr,错误是带 `hint` 的 JSON;③ 回答格式遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md)。

## ⚠️ 使用前必读

- 上游是金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;
- **保持人类调用频率**,勿高频轰炸;全链路零账号/零 cookie/零点数是红线;
- 本仓库不含任何凭据;完整安全声明见[文末](#安全)。仅供个人学习使用。

## 为什么选 kd?

- **为 agent 原生设计** —— 默认 JSON、stdout=数据/stderr=进度、错误即数据(`hint` 带修复指引)、退出码 0/1/2、永不交互;`kd manifest` 一次自发现,agent 不读文档即会用
- **免费且无门槛** —— 官方 AI 问答要登录+点数,kd 全链路匿名,零账号零点数
- **覆盖面完整** —— 官方文档、社区问答帖(含采纳回答与追问链)、社区文章、官方 AI 分享对话,四种来源一次打通
- **合成不被绑死** —— `kd ask` 给资料包,你的 AI 按规范写回答;或 `kd ai` 接你自己的 OpenAI 兼容通道一步合成,不可用自动降级
- **回答有规范** —— [ANSWER-SPEC](docs/ANSWER-SPEC.md) 对齐官方 AI 样例:原因分析→分步方案→操作边界、表格、[n] 编号引用、资料未覆盖诚实声明
- **零依赖部署** —— 服务与 CLI 均为 Python 纯标准库,单文件,pip 都不用装

## 功能

| 域 | 能力 |
|---|---|
| 🔍 检索 | 三种实体(官方文档/社区问答/社区文章)一次全返回,按产品线(旗舰版/苍穹/企业版)路由 |
| 📖 全文 | 知识库文档全文、问答帖全文(问题+全部回答+追问链,采纳优先)、社区文章全文 |
| 📦 资料包 | `kd ask` 检索+深读 topK 全文一站带回,交当前 AI 合成带引用回答 |
| 🤖 AI 合成 | `kd ai` 关键词规划→检索→按 ANSWER-SPEC 合成,接你自己的模型通道,自动降级 |
| 💬 分享对话 | 读官方 AI 分享对话全文(含引用来源),可作官方效果样本 |
| 🧭 自发现 | `/manifest` 机器可读能力清单,agent 首触即会用 |

## 安装

### ① 全套(推荐,新机器一条命令)

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.ps1 | iex
```

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.sh | bash
```

装什么:①检索服务(`~/.kingdee-kit/service`,自动拉起) ②kd CLI(`~/.kingdee-kit/bin`,加入 PATH)
③技能(`~/.agents/skills`) ④自动跑 22 项回归验证。开关:`-NoPath -NoSkills -NoStart -InstallRoot -Port`。

### ② AI Agent 三行接入(已有服务/CLI 的机器)

```text
1. 跑 kd manifest —— 读端点、参数与示例
2. 有技能的 agent:装下面技能后问金蝶问题即自动走 kd;没有技能:把 manifest 输出读进上下文
3. 回答按 docs/ANSWER-SPEC.md 的格式(带 [n] 引用,资料未覆盖要声明)
```

只要技能(机器上已有 kd/服务):把 `skills/kingdee-knowledge/skills/kingdee-knowledge/` 复制到
`~/.agents/skills/`;或 `npx skills add Drivpe/kingdee-knowledge-kit`;或 ZCode 插件面板
Settings → Plugins → Discover → 添加本仓库 GitHub 地址 → Get。

### ③ 开发者

```bash
git clone https://github.com/Drivpe/kingdee-knowledge-kit.git && cd kingdee-knowledge-kit
powershell -File install.ps1 -InstallRoot D:\kit -NoPath -NoSkills   # Windows 自定义
bash install.sh --root ~/kit --no-path --no-skills                   # *nix 自定义
```

## kd 命令(7 个)

| 命令 | 作用 |
|---|---|
| `kd search "关键词" [--product 93] [--type answer] [--size 10]` | 检索:官方文档/社区问答/文章三种实体全返回 |
| `kd read <id> [--kind knowledge\|answer\|article]` | 读全文:`--kind` 照抄 search 结果的 `type`,零翻译 |
| `kd ask "问题" [--topk 4]` / `--kw "词1" --kw "词2"` | 一站式资料包(检索+深读 topK 全文),交给当前 AI 合成 |
| `kd ai "问题" [--topk 4]` | 关键词规划→检索→按 ANSWER-SPEC 合成带引用回答;模型通道不可用自动降级为资料包(`fallback:true`) |
| `kd share <官方分享短链>` | 读官方 AI 分享对话全文(含引用) |
| `kd manifest` / `kd health` | 机器可读能力清单 / 存活检查 |

`--product` 语义:93=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、0=不过滤(等价省略参数)。

## 进阶

### JSON 契约(AI-first 七原则)

- 默认输出 JSON;**stdout=数据,stderr=进度**(无 ANSI 色码)
- 错误是 JSON `{"code","message","hint","example"}`,`hint` 给修复指引(服务挂时含重启命令)
- 退出码:`0` 成功 / `1` 服务或上游错误 / `2` 用法错误
- 永不交互;两级 `--help` 带示例;大输出标 `truncated` 并指路下一步
- agent 自发现:`kd manifest` 返回端点/参数/实体/示例,无需读文档

### kd ai 模型通道

`kd ai` 需要 OpenAI 兼容端点(接你自己的渠道,零官方点数):

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `KAI_BASE` | `http://127.0.0.1:4090` | OpenAI 兼容基址,**勿带 `/v1`**(路径按原样透传拼接) |
| `KAI_MODEL` | `glm-5.3-flash` | 模型名 |
| `KSEARCH_URL` | `http://127.0.0.1:4097` | 检索服务基址(kd 与回归测试共用) |

流程:问题 →(LLM①)2-4 个检索关键词 → 匿名检索深读 topK →(LLM②)按 ANSWER-SPEC 合成,
输出含 `answer`(Markdown)、`keywords`、`references`。通道不可达/合成失败自动降级:
返回 `kd ask` 资料包 + `fallback:true` + `fallbackReason`,调用方 AI 拿包自己写,不产生硬失败。

### 回答规范

所有合成回答(包括你自己的 AI 拿资料包写回答时)遵循 [docs/ANSWER-SPEC.md](docs/ANSWER-SPEC.md):
三段式结构(原因分析→解决方案→操作边界)、表格、`[n]` 编号引用+文末来源列表、资料未覆盖诚实声明。

## 回归

改服务/CLI 后:`python tests/verify_ksearch.py`(约 1 分钟,22 项;`KSEARCH_URL`/`KD_PY` 可指向任意环境;
kd ai 检查用本地假 OpenAI 兼容端点,不需要真实模型通道)。

## 安全

- **本仓库不含任何凭据**。cookie、账号 token、API key(如 model-map.json)、日志、含本机路径的笔记一律不入库,`.gitignore` 已按模式拦截——推送前 `git status` 再核对一遍
- 上游为金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;保持人类调用频率,勿高频轰炸
- `kd ai` 的模型通道走你自己的服务与密钥,密钥只放环境变量,不写入任何文件
- 公开仓库等于公开接口细节,建议私有库,或接受"仅供个人学习使用"的公开声明

## License

MIT
