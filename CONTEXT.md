# kingdee-knowledge-kit

金蝶官方知识的本地匿名检索套件。第一用户是 agent(个人客户端里的 AI),人类几乎不直接使用;kd CLI 是唯一入口,检索服务是唯一事实源。

## Language

**kd**:
本套件的 CLI 二进制名。AI-first:stdout 只出 JSON 数据、进度走 stderr、错误是带 hint 的 JSON、永不交互。
_Avoid_: kingdee-cli、kdk

**检索服务(kingdee-ksearch-service)**:
唯一内核/唯一事实源,本地封装金蝶云社区匿名接口;改能力或适配上游变更都只改它。
_Avoid_: L1、后端、server(泛称)

**三种实体**:
检索结果的三个类型 `knowledge`(官方文档)/ `answer`(社区问答帖)/ `article`(社区文章),`type` 字段贯穿 search 与 read,一一对应。
_Avoid_: 三级链路、知识类型

**资料包**:
`kd ask` 的返回物:检索命中 + 按相关度深读的 topK 全文,合成回答所需的全部素材;调用方(或 kd ai)拿包即写。
_Avoid_: RAG 结果、上下文

**回答规范(ANSWER-SPEC)**:
合成回答必须遵循的格式单一事实源,对齐官方 AI 样例:原因分析→分步方案→操作边界、表格、[n] 编号引用、资料未覆盖时诚实声明。
_Avoid_: 输出模板、prompt(泛称)

**降级(fallback)**:
kd ai 的模型通道不可用时,自动改为返回资料包并带 `fallback:true`;任何情况下都有可用数据。
_Avoid_: 失败、报错(降级不是错误)

**模型通道**:
kd ai 合成回答用的 OpenAI 兼容端点(KAI_BASE/KAI_MODEL),接用户自己的渠道;与服务无关、可缺席。
_Avoid_: LLM 服务、AI 服务(泛称)

**匿名链路**:
全链路零账号、零 cookie、零官方点数;保持人类调用频率是红线。
_Avoid_: 免登录(含免费注册义)、爬虫
