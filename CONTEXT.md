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

**corpus 语料目录**:
`~/.lingeebuild/corpus/`:一文档一 md + front-matter(id/type/url/title/updatedAt/discovered_by/stub?),
read/ask 深读同步写穿全文,时间网格与官方分享引用落 stub(标题+摘要,正文按需深读覆盖)。
**给 agent 和人的检索语料**:agent 用 rg 直接搜,命中即读、引用原链接;发现层成果的落脚点。
_Avoid_: 语料库(指 sqlite 时)、知识库(泛称)

**缓存语料库(上游缓存)**:
`/ask`、`/read` 结果自动沉淀的本地 sqlite 永久库(data/ksearch.db):明细全文 + chunk 切片。
检索先查缓存,命中零上游调用。**v5 起缩编为纯上游缓存**(网络去重:不重复打上游),不再扩展;
FTS5/chunks 写入与 `local=1` 端点冻结开发(deprecated),检索角色由 corpus+rg 接管(ADR-0004)。
默认关闭(opt-in),用户本机经启动器 `KSEARCH_INDEX=on` 开启。
_Avoid_: 临时缓存(它跨会话永久)、向量数据库(已冻结待墙)

**发现层**:
把官方搜索索引外的文档带进 corpus 的机制,四条腿:**全量快照**(`corpus_fullscan.py`,93+87 全部文档
标题+摘要 stub,一次性,1 请求/秒、≤7500 请求,红线豁免需用户拍板)+ 时间网格(`discovery_sweep.py`,
词表×sortsType=2 时间倒序,手动触发,≤200 请求/轮)+ 官方分享引用自动落盘 + usage 沉淀;
图游走(recommendArray)已剔除——匿名不可达(ADR-0004 增补)。
_Avoid_: 爬虫、定时任务(手动触发,无后台)、图游走(已剔除)

**chunk 切片**:
把文档全文按标题感知(【问题描述】/【概述】/【操作步骤】等结构)切成带序号与标题的段落。`/ask` 每源附 top3 相关 chunk,合成引用可到 `[n](chunk#m)`,对齐官方 AI 的 chunk 级引用粒度。
_Avoid_: 分段(泛称)、embedding(切片不向量化)

**评测集**:
`data/eval/evalset.json`,双层:**usage 层=正式金标**(真实使用产生的问题,gold=解题过程读全文核实的文档,达标只认这层)+ **reference 层=官方 AI 对话金标**(仅参考,不作达标依据——官方语义索引含匿名检索不可达文档,且正式使用须脱离对官方 AI 的依赖)。`scripts/run_eval.py` 分层出数(recall@5/@10、MRR、时延);管线改动必须过评测,且**先标金、后测管线**(金标在动管线前定死,防自证循环)。
_Avoid_: 测试集(verify 是回归,评测是质量)、官方对齐(达标口径已脱离官方 AI)
