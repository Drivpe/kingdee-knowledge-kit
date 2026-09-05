# Spec:kingdee-knowledge-kit v2.0 —— AI-first 重构

> 状态:ready-for-agent · 来源:2026-09-05 grill 问答定稿 · 上游素材:交接文档 13/14、官方分享样例 clzL8

## Problem Statement(问题陈述)

v1.0 把定位写成「零 LLM 依赖,合成回答由调用方 AI 完成」,但真实的主要用户是**个人客户端里的 agent**——人类几乎不直接使用。这带来四个错位:

1. 语义识别→检索→合成的 `kd ai` 闭环(已实测打通:23 秒出带表格与 [n] 引用的回答)在打包 v1.0 时被剥离,终端场景拿到的资料包没人帮读;
2. README 没有 AI agent 的阅读动线,AI-first 契约(stdout=数据/stderr=进度/错误即数据)埋在中段,agent 无法三行内上手;
3. 读全文命令面碎成 question/answer/article 三条,`article --kind` 一词双义,agent 从 search 结果到调用要二次翻译;
4. 没有回答格式规范,各客户端 AI 合成的回答风格随机,与官方 AI 样例(分级标题+分步方案+表格+编号引用+诚实声明)不对齐。

## Solution(方案)

v2.0 重构:定位改写为「**agent 的金手指——金蝶官方知识匿名检索**,人几乎不用碰」。命令面收敛为 7 个扁平命令(`read` 合并原三条);`kd ai` 重新入仓并带自动降级;新建回答规范单一事实源并对齐官方样例;README 按官方 CLI 风格重写(仅中文)且风险声明前置;技能目录更名 `skills/`;项目术语表(CONTEXT)与关键决策档案(ADR)入仓;版本升 v2.0。

## User Stories(用户故事)

1. 作为个人客户端里的 agent,我想 `kd manifest` 一次返回全部命令/参数/示例,以便首次接触 kd 不读文档即可正确调用。
2. 作为 agent,我想 `kd search` 一次返回官方文档/社区问答/社区文章三种实体,以便单次检索后按 type 路由深读。
3. 作为 agent,我想 `kd read` 的 `--kind` 直接照抄 search 结果里的 `type` 字段,以便从检索到读全文零思考。
4. 作为 agent,我想 `kd ask` 一站式带回 topK 全文资料包,以便单次调用即拥有合成带引用回答的全部素材。
5. 作为 agent,我想在模型通道可用时用 `kd ai` 直接获得合成好的 Markdown 回答,以便省去我自己二次合成。
6. 作为 agent,我想在模型通道不可用时 `kd ai` 自动降级返回资料包并带 `fallback:true` 标记,以便任何情况下都有可用素材。
7. 作为 agent,我想所有错误都是 `{code,message,hint,example}` JSON 且 hint 给修复指引,以便按提示自愈而不是瞎猜。
8. 作为 agent,我想 stdout 只有数据、进度/日志全走 stderr 且无 ANSI 色码,以便解析永不被污染。
9. 作为 agent,我想退出码遵守 0/1/2 契约,以便脚本化判断成败与错误类别。
10. 作为 agent,我想回答遵循 ANSWER-SPEC 的结构(原因分析→分步解决方案→操作边界,表格与 [n] 编号引用,文末来源列表),以便回答质量对齐官方 AI 样例。
11. 作为 agent,我想在资料未覆盖问题时得到诚实声明而非编造,以便信任输出。
12. 作为没装技能的 agent,我想 README 里有「Note for AI assistants」段,以便读三行就知道先跑 `kd manifest`。
13. 作为终端人类用户(少数场景),我想 `kd share <官方分享短链>` 读出官方 AI 分享对话全文含引用,以便收集官方效果样本。
14. 作为终端人类用户,我想两级 `--help` 带示例、大输出带 truncated 标记并指路下一步,以便偶尔亲手敲命令也能用。
15. 作为维护者,我想 CONTEXT.md 术语表与 ADR 决策档案入仓,以便决策不随会话交接文档散失。
16. 作为维护者,我想回归测试覆盖新命令面(read 三类/ai 正常与降级/manifest 清单),以便重构后一键验收。
17. 作为插件市场用户,我想技能目录更名后 marketplace 清单同步,以便插件仍可被市场发现与安装。
18. 作为新机器用户,我想一键安装脚本同步新目录与 ai 环境变量说明,以便装完即用。
19. 作为关注合规的维护者,我想「使用前必读」风险声明前置到 README 显眼处,以便使用者在第一屏知晓非官方逆向接口的约束。

## Implementation Decisions(实现决策)

- **命令面(7 个,扁平)**:`search / read / ask / ai / share / manifest / health`。`read` 合并原 question/answer/article 三命令;`--kind` 取值 `knowledge|answer|article`,默认 knowledge,与检索实体的 `type` 一一对应,消掉 `--kind article` 双义。不做域化命名(规模不需要,迁移成本不值)。
- **kd ai 入仓**:环境变量 `KAI_BASE`(任意 OpenAI 兼容端点,不带 /v1)、`KAI_MODEL`;内部两段 LLM 调用(问题→检索关键词转写;资料→带引用合成);通道不可达/上游报错时自动降级为 `kd ask` 资料包输出并带 `fallback:true`;调本机代理需伪装 UA(铁律:python-urllib 默认 UA 被上游 Cloudflare 403)。
- **回答规范单一事实源**:新建 ANSWER-SPEC 文档,内容对齐官方分享对话样例:①结构化 Markdown——问题原因分析 → 解决方案(编号步骤)→ 操作边界/适用范围;②适当用表格;③正文 `[n]` 编号引用 + 文末来源列表(标题+实体类型);④资料未覆盖时明确声明。kd ai 内置提示词、技能文档、README 进阶章三处引用同一份规范,不各写一份。
- **目录**:技能从 `plugins/` 迁至顶层 `skills/`,marketplace 清单的 git-subdir path 同步更新(项目无存量用户,无兼容包袱)。
- **README**:仅中文单文件,骨架对标 larksuite/cli:徽章 → 锚点导航 → 「为什么选」→ 功能表 → 安装(含 AI Agent 流程)→ 命令表 → 进阶(JSON 契约/输出规范引用)→ ⚠️使用前必读(前置精简版:非官方逆向、可能失效、限频)→ License。技能与插件描述文案同步新定位。
- **治理**:仓库根 CONTEXT.md 术语表(kd、资料包、三种实体、回退等);docs/adr/ 两条:0001 剔除 MCP、0002 kd ai 入仓。
- **服务端不动接口**:检索服务只更新 /manifest 里的 CLI 命令清单,端点/参数面保持 v3.2。
- **不加 `--text` 人类可读输出**:保持纯 JSON 的 AI-first 纯粹性;ai 的 answer 字段即 Markdown 正文。
- **版本 v2.0**,MIT 不变;「零 LLM 依赖」卖点改写为「零官方 LLM/零点数,模型通道接你自己的」。

## Testing Decisions(测试决策)

- **单一接缝**:沿用现有端到端回归(CLI 的 stdout JSON 契约 → 检索服务 → 上游匿名接口),即现有 19 项检查所在的那条链;不新增单元测试层。
- 好测试只测外部行为:退出码、JSON 形状、stdout/stderr 分离、错误 hint 存在性;不测实现细节。
- 新增断言:read 对三种实体的读取、ai 正常路径(本地起一个假 OpenAI 兼容端点)与降级路径(fallback:true)、manifest 命令清单与新命令面一致。
- 回归脚本沿用现有可配置环境变量跑法,可指向任意环境。

## Out of Scope(不做)

- 答案与官方对比/拟合度优化(回归评测集、提示词校准、检索信号重排)——交接文档 14 明示后续再做;
- LAN 暴露、服务原生 /mcp;
- 英文 README、中英双语维护;
- 服务端接口面改动(逆向端点、参数语义均不变)。

## Further Notes(备注)

- 上游为金蝶云社区非官方逆向接口,「零账号/零 cookie/零点数、人类调用频率」红线不变;
- issue tracker 未配置,spec 落仓库内文档;
- 拆票:`.scratch/v2-ai-first-refactor/issues/` 下 10 张,阻塞边见各票。
