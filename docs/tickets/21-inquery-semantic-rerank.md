# 票 #21:查询内语义重排(本地 bge-small ONNX → topK 深读选择)

状态:**🔄 重开为「待测路线」(2026-09-16 复核)**——原关闭依据「评测门 ❌ 未通过(词汇鸿沟存活率 1.0→0.0)」经复核不成立:该 0.0 是服务端预算耗尽事故,见下文「判定依据复核」。**路线有效性未验证**,既不能判定其有害,也不能判定其无害;需以新评测重测。服务仍保持默认 OFF。 | 依赖:无(独立于 #17-#20) | 优先级:P2

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
- ④ ON(临时 env 开启,同一条缓存 ask):`semanticRerank{enabled:true, reordered:true, candidates:25, reason:"ok"}`;首调(含模型加载)1467.7ms,热调 790.2ms(25 候选,秒级内);**深读选择变化实锤**:rank2 由 584330946073958912(语义分 0.496)换成语义分最高的 636272302971087872(0.5649);回答 ok、detail 全文正常。展示排序规则未动。
- ⑤ 降级路径:模型目录缺失时 rerank_candidates 返回原序列不变,reason="model files missing: ...",不影响回答可用性(进程内实测)。
- ⑥ 上游纪律:全程真实上游请求仅 1 个(开启后深读新候选 636272302971087872 首读),符合 ≤4 限制;缓存命中零上游。

## 遗留 / 未完
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
