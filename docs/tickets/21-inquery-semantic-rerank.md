# 票 #21:查询内语义重排(本地 bge-small ONNX → topK 深读选择)

状态:**❌ 已废止(2026-09-29 复核)**——**「待测」这个状态不可达**,已撤销 2026-09-16 的「🔄 重开为待测路线」。
判定依据 = **ADR-0011 决策 4「删除 `semantic_rerank`」**(`docs/adr/0011-decommission-http-service.md:68-74`):
实现载体 `service/semantic_rerank.py` 与接入点 `service/kingdee-ksearch-service.py` **均已删除**,
"新评测重测"所依赖的评测体系(`run_eval.py` / `scripts/`)亦**整体删除**。
**零可达性**:无代码、无模型依赖、无评测harness、调用方(every `kd` 进程)不复存在。
本票不再有可做工作,保留文件留痕。详见文末「台账复核(2026-09-29)」。
(历史状态沿革,原文保留 ↓)**🔄 重开为「待测路线」(2026-09-16 复核)**——原关闭依据「评测门 ❌ 未通过(词汇鸿沟存活率 1.0→0.0)」经复核不成立:该 0.0 是服务端预算耗尽事故,见下文「判定依据复核」。**路线有效性未验证**,既不能判定其有害,也不能判定其无害;需以新评测重测。服务仍保持默认 OFF。 | 依赖:无(独立于 #17-#20) | 优先级:P2(**已废止**)

## 改动
CONTEXT.md 词条「查询内语义重排」落地:ask 在深读 topK 前对候选做 query-doc 本地语义打分(bge-small-zh ONNX,~100MB,零上游),按分重排深读顺序;展示排序不变(answer 优先)。

### 实现(2026-09-07)
- **打分器** `service/semantic_rerank.py`(新文件):本地 bge-small-zh-v1.5 ONNX(Xenova 导出,94.8MB)+ tokenizers 分词,CCLS 池化 + L2 归一,余弦 = 内积。**查询侧加指令前缀「为这个句子生成表示以用于检索相关文章:」,文档侧不加**(bge 系模型用法)。候选侧文本 = title+snippet(深读前可用面);≤50 条,超时 5s;进程内懒加载单例,限 2 CPU 线程。
- **接入点** `service/kingdee-ksearch-service.py` 的 `ask_bundle()`:插在 `_rrf_fuse` → `ranked = sorted(...)` **之后、`_select_top(ranked, top_k)` 之前**——只重排深读 topK 的选择序列;下方 `sources` 展示排序(三层路由 displayOrder + fusedScore)与 `fetch_order` 一行未动。
- **开关与降级**:`KSEARCH_RERANK_SEMANTIC` 默认 **off**;模型文件缺失 / onnxruntime 未装 / 打分异常 / 超时>5s 一律静默降级为原排序,ask 返回体附 `semanticRerank{enabled,reason,reordered,candidates,scores,elapsedMs}`;ask_bundle 外再套一层 try/except 兜底。`kd health` 增 `rerankSemantic` 字段(开关 + 模型加载状态 + 模型目录);/manifest pipeline.env 增说明。
- **打分日志**:每候选分数进服务日志+stderr(`SEMANTIC-RERANK: reranked N candidates in Xms; top scores: ...`)。
- 模型落 `~/.lingeebuild/models/bge-small-zh-v1.5/`(hf-mirror.com 直连下载 onnx/model.onnx + tokenizer.json 等;经 install.ps1 可选下载,不捆绑——当前为手动落位);依赖 onnxruntime 1.29.0 + tokenizers 0.23.2 装入 Python 3.12(%LOCALAPPDATA%\Programs\Python\Python312)。

## 验收
过评测门:词汇鸿沟存活率与根因可解释率显著提升(gold 进 topK 深读),usage 层池化抽样对照;无提升即下线(软约束不硬上)。

## 冒烟证据(2026-09-07,缓存命中 ask:`kd ask "BOM维护" --kw "BOM维护" --product 93 --topk 2`)
- ① py_compile:仓库版 service/kingdee-ksearch-service.py + service/semantic_rerank.py,及部署副本 `~/.lingeebuild/config/`(已 cp 同步)均通过。
- ② 重启后 `kd health`:`rerankSemantic {enabled:false, modelLoaded:false, reason:"not-loaded", modelDir:"...bge-small-zh-v1.5", timeoutMs:5000, maxCandidates:50}`;开启重启后 enabled:true、模型懒加载成功(日志 inputs=[input_ids,attention_mask,token_type_ids], output=last_hidden_state)。
- ③ OFF 基线:上游 0 请求、cacheHits 2、8.6ms,sources 顺序与现状逐位一致,`semanticRerank.reason="switch off"`——**行为与现状完全一致**。
- ④ ON(临时 env 开启,同一条缓存 ask):`semanticRerank{enabled:true, reordered:true, candidates:25, reason:"ok"}`;首调(含模型加载)1467.7ms,热调 790.2ms(25 候选,秒级内);**深读选择变化实锤**:rank2 由 584330946073958912(语义分 0.496)换成语义分最高的 636272302971087872(0.5649);回答 ok、detail 全文正常。展示顺序未动。
- ⑤ 降级路径:模型目录缺失时 rerank_candidates 返回原序列不变,reason="model files missing: ...",不影响回答可用性(进程内实测)。
- ⑥ 上游纪律:全程真实上游请求仅 1 个(开启后深读新候选 636272302971087872 首读),符合 ≤4 限制;缓存命中零上游。

## 遗留 / 未完(2026-09-07 当次记录;**下述"待办"已于 2026-09-29 判定不可达,见文末「台账复核」**)
- **评测门(未完)**:词汇鸿沟存活率 / 根因可解释率对照跑分——**待用户拍板跑评测**;过门前 `KSEARCH_RERANK_SEMANTIC` 保持默认 off。
- 25 候选热打分 ~790ms,若候选常满 50 条需留意时延(可减到更小 token 截断或先 title-only 粗筛)。
- 模型当前手动落位,install.ps1 可选下载步骤未加(票面要求"模型经 install.ps1 可选下载"——install.ps1 属禁改范围外文件但本次未动,待用户拍板评测后再补)。
- 部署副本已同步(~/.lingeebuild/config/kingdee-ksearch-service.py + semantic_rerank.py),服务已恢复默认 OFF 运行。

## 判定依据复核(2026-09-16):原「❌ 未通过」不成立

原判定(2026-09-07)依据 `docs/eval-rerank-off.json` / `-on.json`,结论为
「词汇鸿沟存活率 1.000 → 0.000,全局替换式语义重排为负资产」。

复核发现:**那个 0.000 是上游预算耗尽造成的,rerank 开关未参与该判定。**

| 轮次 | JSON `upstreamCalls` | hardRow `upstream` | 服务日志对应行 |
|---|---|---|---|
| rerank-off | used 16 / cap 38 | 14 | `09-06 18:13:36 ... upstream 14 / 16 \| exhausted False` |
| rerank-on | used **6** / cap 38 | **4** | `09-06 18:18:13 ... upstream 4 / 4 \| exhausted True` |

ON 组实际消耗仅 6,日志明示 **`exhausted True`**——服务端单次 ask 预算被压到 4
(正常上限 16),`/ask` 编排提前收尾,gold 文档 `873372977646105600` 未进 sources。

两组搜索层指标完全相同(`r5/r10/mrr` 均为 0.333),OFF 组 pipeline 为 `{}`(默认态);
差异全部落在 `/ask` 层的上游消耗上,**不是排序行为的差异**。

### 对原根因分析的处理

原「根因」节(表层词污染 / 路由多样性被抹掉)**不来自上述两份 JSON**——
其引用的语义分 `0.5688`、`0.5281` 在公开仓与私有仓均无留档,来源不可复查。
该描述作为**待验证假设**保留,不作为判定依据。若要重测,应重新采集带
`semanticRerank` 明细的证据。

### 重测前置条件

1. **修预算隔离**:`--max-requests` 是脚本层全轮预算,与服务的
   `budget.maxUpstreamPerAsk`(默认 16)是两个独立预算。重测前须确保
   `/ask` 单次调用不被外部压低到 `exhausted True`;建议在评测脚本中对
   `exhausted` 标志做断言,出现即判该轮无效。
2. **扩硬指标样本**:全池仅 1 个 hardMetric 用例(`seed-vocab-gap-bom-27000`),
   n=1 无法支撑任何路线裁决。重测需先扩池。
3. **记录逐路明细**:现有 JSON 只存聚合指标与 `stats: null`,无法离线复算
   融合策略差异。重测应落盘逐路排序结果。

## v2 方向(保留,未实施)
- 混合式:RRF 主序不动,语义分仅作**补位**(topK 尾部空位补语义高分之一,每路 top-1 保深读席位);
- 或语义分只在同分内作 tie-breaker;
- 或路由内重排后再融合(保多样性)。

原结论「全局替换式重排在词汇鸿沟场景是负资产」**依据已失效**,不足以支撑该判断;
v2 与 v1 均属未验证路线。

## 台账复核(2026-09-29):废止

状态由「🔄 重开为待测路线」改为「❌ 已废止」,依据是**载体与评测体系双缺失**——「待测」所需的
最小条件(存在可测的实现 + 存在可跑的评测)两条**同时不成立**。

### 1. ADR-0011 决策 4 原文:`docs/adr/0011-decommission-http-service.md:68-74`

```
### 4. 删除 `semantic_rerank`

删模块与依赖。理由:它要解决的问题(关键词搜不到但语义相关)**已由 ADR-0009 的原句路解决**——
后者零依赖、已上线、有实测背书(原句排第 1 vs 片段融合第 23)。在修好的问题上再修一遍,
且需 96MB 模型 + 两个额外依赖。

**注意**:这不是否定票 #21 的调研价值,而是判定其**实现路径**已被更廉价的方案取代。
```

本 ADR 状态「已采纳」;`docs/adr/0011:139` 另载后果:「**`semantic_rerank` 路线关闭**」;
`docs/adr/0011:149` 载备选取舍:「承载 `semantic_rerank` 并在启用时再服务化:被否——**先删代码**……留着未启用的模块是"永不亮的路"」。
另注:`docs/adr/0011:29-30` 记录当年实测事实——该模块**默认关**、**模型从未下载**、`tokenizers` 未安装、**评测未过门**。

### 2. 实现载体:均已删除

- `ls service/` → **`No such file or directory`**(整目录不存在);
- `ls service/semantic_rerank.py` / `ls service/kingdee-ksearch-service.py` → 同上;
- `git log --diff-filter=D --name-only -- service/` → **唯一删除提交 `6afa727`**,同时删除
  `service/semantic_rerank.py`(删除前 **201 行**)、`service/kingdee-ksearch-service.py`、
  `service/docstore.py`、`service/query_routes.json`;
- `git log -1 --format=%ad 6afa727` → **2026-09-17 13:28:32 +0800**;
- 删除前存在的证据:`git show 6afa727^:service/semantic_rerank.py | wc -l` → `201`;
  接入点行号可从删除前文件复原:`KSEARCH_RERANK_SEMANTIC` 在
  `service/kingdee-ksearch-service.py:1127`、`semanticRerank` 返回体在 :1052、
  `rerankSemantic` 健康字段在 :1295(均随该提交一并消失)。

### 3. 评测体系:已删除

- `ls run_eval.py` → **不存在**;`ls scripts/` → **不存在**;`ls tools/` → **不存在**;
- `git log --diff-filter=D --name-only -- run_eval.py scripts/` → `6afa727` 删除
  `scripts/run_eval.py`、`scripts/corpus_fullscan.py`、`scripts/discovery_sweep.py`、
  `scripts/releasenotes_ingest.py`、`scripts/releasenotes_probe.py`、`scripts/start-service.ps1`、
  `scripts/start-service.sh`(另 `43990dc` 删 `scripts/check_core_surface.py`);
- `data/` 现存**仅** `m-probe-gold.json`(2793 B),**无用例池**;`docs/eval-runs-v4/` 仅 6 个 v4 期 md;
- 全仓现存可执行 `.py` 只有 `src/kd/**`(内核)与 `tests/kd_regression.py`(回归),**无评测 runner**;
- **权威旁证**:同一事实已由 ADR-0017 决策 3 追认——
  `docs/adr/0011-decommission-http-service.md:86-91` 补记:「`run_eval.py` 连同
  `corpus_fullscan.py` / `discovery_sweep.py` / `releasenotes_ingest.py` / `releasenotes_probe.py`
  已随去服务化**整体删除**(`scripts/` 目录不存在,`git log --diff-filter=D` 可证)」;
  `docs/adr/0011:125-126` 另载「验收以**手工用例**为主……**不依赖 `run_eval`**(**评测体系已因精度不足搁置**)」。
- 本票自身「重测前置条件」三条现已**全部不可满足**:① 所依赖的服务端
  `budget.maxUpstreamPerAsk`(`service/kingdee-ksearch-service.py`)已不存在;
  ② 「扩硬指标样本」所需用例池随 `data/eval/` 一并处置(`docs/specs/2026-09-16-去服务化重构.md:128`);
  ③ 「评测脚本」「落盘逐路明细」亦随 `run_eval` 消失。

### 4. `CONTEXT.md` 词条:仍在,已标废除

`CONTEXT.md:251-260` 保留词条「**查询内语义重排**」,但正文已删除线化并标
「**已废除(2026-09-16,ADR-0011)**:`semantic_rerank` 模块删除」;
`:255-256` 另载「将来若重新启用语义重排,冷启动重载模型会**反向要求服务化**,须重新评估 ADR-0011」。
→ **该词条是档案,不是现行能力。** `:260` 的 `_Avoid_` 亦已把「语义搜索」列为夸大用词。

### 5. 残留引用清单

见下表。**除本票与历史 ADR/specs 外,零活跃引用**;`src/kd/**` 全目录
`rg 'semantic_rerank|RERANK_SEMANTIC|semanticRerank|rerankSemantic|rerank|semantic'` → **零命中**。

| 文件:行 | 内容 | 判定 |
|---|---|---|
| `docs/adr/0011-decommission-http-service.md:68` | 决策 4 标题「删除 `semantic_rerank`」 | **活跃**(现行决策,即废止依据) |
| `docs/adr/0011-decommission-http-service.md:29` | `KSEARCH_RERANK_SEMANTIC=off` 实测 | 历史(记录删除前提) |
| `docs/adr/0011-decommission-http-service.md:48` | `docstore` + `semantic_rerank` 加 HTTP 包装 | 历史 |
| `docs/adr/0011-decommission-http-service.md:139` | 「`semantic_rerank` 路线关闭」 | **活跃**(后果声明) |
| `docs/adr/0011-decommission-http-service.md:149` | 备选被否「保住后在启用时再服务化」 | 历史(已定的取舍) |
| `CONTEXT.md:251-256` | 词条「查询内语义重排」+ 已废除标注 | 历史(档案,明确标废除) |
| `README.md:64` | 「`semantic_rerank` 与 `run_eval` 评测体系全部删除」 | **活跃**(对外现状陈述) |
| `docs/specs/2026-09-16-去服务化重构.md:6/13/15/24/51/81/162/191` | 重构规格中的删除计划与理由 | 历史(specs 留痕) |
| `docs/specs/2026-09-16-去服务化-残留引用清单.md:31/64` | 残留清单:约 60 处,处置判据 | 历史 |
| `docs/research/2026-09-06-official-vector-recall.md:46` | bge-small-zh-v1.5 候选调研 | 历史(research 留痕) |
| `docs/eval-*.md` / `docs/eval-*.json` | `rerank=false` | **历史,且非本物** |
| `docs/tickets/21-inquery-semantic-rerank.md` | 本票 | 留痕(本条除外) |

⚠️ **同名不同物,勿误判**:`docs/eval-report-v4.md:18` 与 `docs/eval-baseline-v6.1-*.md` 里的
`rerank=false` / `KSEARCH_RERANK=1` 是**信号重排**(手工信号权重:标题命中/采纳/浏览量,
`docs/eval-report-v4.md:18` 明述"信号重排:评测否决"),与**语义重排**(`semantic_rerank`,bge ONNX)
**是两回事**。本仓已把此坑写入 `docs/specs/2026-09-16-去服务化-残留引用清单.md:64` 作为既有教训。
故上表**不得**把 `rerank=false` 计为语义重排残留。

### 结论

**一处必须澄清的精确定性**(避免夸大或低估):

- **指标口径仍存活,但以手工形式**:`CONTEXT.md:175-178` 明示「`data/eval/` 全目录与 `scripts/run_eval.py`
  随去服务化删除」,**验收改以两条手工硬指标为准**——正是本票验收节所用的
  **词汇鸿沟存活率** 与 **根因可解释率**。故「评测门」这套**判据**并未消失。
- **但「可评测」的三个要件仍全缺**:① **被评测对象已删**(`semantic_rerank` 模块 + 服务接入点);
  ② **自动化 runner 已删**(`run_eval.py` / `scripts/`,且用例池 `evalset-rerank-gate.json`
  ——`docs/eval-rerank-off.json` 的 `evalset.file`——**已不存在**,`find` 零命中);
  ③ **服务的预算机制已删**(重测前置条件①所依赖的 `budget.maxUpstreamPerAsk`)。
- 「手工硬指标」是**给人/agent 用的验收口径**,**不构成**对一条开关化路线的 A/B 重测能力——
  没有 runner、没有用例池、没有被测实现,无法产出本票验收节要求的对照跑分。

「待测」的**两个必要条件同时不成立**(实现载体已删 + 评测 harness 已删),且 ADR-0011 决策 4
已就「实现路径」作出明确废止裁定(保留其调研价值)。故本票标 **❌ 已废止**,
**不再显示为待测**,不设「待用户拍板」「重测前置条件」等**不可达**承诺——
按本仓硬纪律,不得承诺不存在的能力。

**若将来重新引入语义重排**,须先**重新评估 ADR-0011**(`docs/adr/0011:139`、`CONTEXT.md:255-256`),
且届时要连带重建服务化形态与评测 harness——**那不是本票的残余工作,而是一次新立项**。
