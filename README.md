# kingdee-knowledge-kit

金蝶官方知识的本地检索套件:**一个匿名检索服务 + 一个 AI 友好的 CLI(`kd`)+ 一个跨客户端技能**。
逆向自金蝶云社区公开检索后端,**零账号、零点数、零 cookie、零 LLM 依赖**——资料包拿回来,合成回答由你正在使用的 AI 完成。

```
你的问题(ZCode / Claude / 灵基 / 终端)
        │  kd CLI(8 命令,stdout=JSON,stderr=进度)
        ▼
kingdee-ksearch-service(127.0.0.1:4097,纯标准库,自描述 /manifest)
        │  匿名 GET(检索 / 全文 / 问答 / 分享对话)
        ▼
vip.kingdee.com 官方社区
```

实测效果:检索"金额字段 单价字段 区别" → 读官方文档 + 社区问答全文 → AI 合成带表格与 [n] 引用的回答,附官方链接,全程约 5 秒、零成本。

## 安装(三选一)

**① 全套(推荐,新机器一条命令)**

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/<你>/<仓库>/main/install.ps1 | iex
```

Linux / macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/<你>/<仓库>/main/install.sh | bash
```

或先 clone 再跑:`git clone <仓库> && powershell -File install.ps1`(Windows)/ `bash install.sh`(*nix)。
装什么:①服务(~/.kingdee-kit/service,自动拉起) ②kd CLI(~/.kingdee-kit/bin,加入 PATH)
③技能(~/.agents/skills) ④自动跑回归验证。开关:`-NoPath -NoSkills -NoStart -InstallRoot -Port`。

**② 只要技能**(机器上已有 kd/服务)

- ZCode:设置 → 插件 → Discover → "+" 添加本仓库 GitHub 地址 → Get(本仓库是标准 marketplace/插件格式)
- 或任意 agent(Claude Code/Codex/Cursor/ZCode 通用):把 `plugins/kingdee-knowledge/skills/kingdee-knowledge/`
  复制到 `~/.agents/skills/`;或 `npx skills add <你>/<仓库>`

**③ 开发者**

```bash
git clone <仓库> && cd kingdee-knowledge-kit
powershell -File install.ps1 -InstallRoot D:\kit -NoPath -NoSkills   # 自定义
```

## kd 命令(8 个)

| 命令 | 作用 |
|---|---|
| `kd search "关键词" [--product 93] [--type answer] [--size 10]` | 检索:官方文档/社区问答/文章三种实体全返回 |
| `kd question <questionId>` | 问答帖全文:问题+全部回答+追问链(采纳优先) |
| `kd answer <answerId>` | 单条回答全文 |
| `kd article <id> [--kind article]` | 知识库文档 / 社区文章全文 |
| `kd ask "问题" [--topk 4]` / `--kw "词1" --kw "词2"` | 一站式资料包(检索+深读 topK 全文),交给当前 AI 合成 |
| `kd share <官方分享短链>` | 读官方 AI 分享对话全文(含引用) |
| `kd manifest` / `kd health` | 机器可读能力清单 / 存活检查 |

CLI 契约(AI-first):默认输出 JSON;stdout=数据、stderr=进度;错误是 JSON 且 `hint` 带修复指引;退出码 0/1/2;永不交互。
agent 无需读文档,`kd manifest` 一次自发现。

## 客户端接入

- **有技能的 agent**(装了上面技能):问金蝶问题即自动走 kd
- **任意能跑命令的 agent**:把 `kd manifest` 的输出读进上下文即可正确使用
- **自己人肉用**:直接敲命令,`--help` 两级帮助带示例

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `KSEARCH_URL` | `http://127.0.0.1:4097` | 服务基址(kd 与 tests 共用) |

服务端口由启动脚本的 `-Port`/第一个参数指定;`--product` 语义:93=星空旗舰版、87=苍穹、1=星空企业版/标准版、0=不过滤。

## 回归

改服务/CLI 后:`python tests/verify_ksearch.py`(约 1 分钟;`KSEARCH_URL`/`KD_PY` 可指向任意环境)。

## SECURITY

- **本仓库不含任何凭据**。cookie、账号 token、API key(如 model-map.json)、日志、含本机路径的笔记一律不入库,`.gitignore` 已按模式拦截——推送前 `git status` 再核对一遍
- 上游为金蝶云社区**非官方逆向接口**:无鉴权承诺,官方升级可能导致失效;保持人类调用频率,勿高频轰炸
- 公开仓库等于公开接口细节,建议私有库,或接受"仅供个人学习使用"的公开声明

## License

MIT
