# 票 #21:查询内语义重排(本地 bge-small ONNX → topK 深读选择)

状态:**🔒 已关闭归档(2026-09-07,用户拍板)**——评测门 ❌ 未通过(词汇鸿沟存活率 1.0→0.0),全局替换式语义重排为负资产;服务保持默认 OFF,代码保留待未来语料/模型升级再议(v2 混合式方案见文末) | 依赖:无(独立于 #17-#20) | 优先级:P2

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

## 评测门判定(2026-09-07,用户拍板开跑):❌ 未通过,保持 OFF

对照(单例金标 seed-vocab-gap-bom-27000,证据 docs/eval-rerank-off.json / eval-rerank-on.json):

| 指标 | OFF 基线 | ON | 判定 |
|---|---|---|---|
| 词汇鸿沟存活率 | **1.000**(1/1) | **0.000**(0/1) | ❌ 恶化 |
| 根因可解释率 | 1.000 | 0.000 | ❌ 恶化 |
| recall@5 | 0.333 | 0.333 | 持平 |
| 上游消耗 | 16 | 6 | - |

根因(复现实锤,kd ask 资料包 semanticRerank 字段):
1. **表层词污染**:问句含「平方」,语义分冠军竟是「长×宽计算平方数」辅助属性问答(0.5688),金标文档(生产单位数量,0.5281)被挤出 top-4 深读线——bge 对症状词的表层歧义没有免疫力;
2. **路由多样性被抹掉**:RRF 多路融合的金标红利(「生产单位数量」实体路靠前)被全局语义重排覆盖,topK 被"平方"噪声文档占据。

## v2 方向(待拍板,未实施)
- 混合式:RRF 主序不动,语义分仅作**补位**(topK 尾部空位补语义高分之一,每路 top-1 保深读席位);
- 或语义分只在同分内作 tie-breaker;
- 或路由内重排后再融合(保多样性)。
结论:全局替换式重排在词汇鸿沟场景是负资产,**保持默认 OFF**;v2 迭代需重新过本评测门。
