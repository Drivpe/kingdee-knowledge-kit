# kd 检索基线评测(部分轮,2026-09-16)

> **本文件是一次未跑完的评测记录**,保留原因:其分层结果暴露了一个此前未被注意的
> 口径问题(见文末「关键观察」)。完整基线待上游预算放宽后重跑。

## 运行条件

- 评测集:`data/eval/evalset.json`(池 28 例;usage=正式金标 14 例 / reference=官方对话 14 例)
- 配置:`baseline`(服务 v6.1,多路 ask、`rerank=false`、`cache=false`)
- 参数:`--topk 4 --max-requests 40 --sleep 1`
- 结果:**上游预算 40 耗尽,20/28 例完成,硬指标(vocab-gap / root-cause)全部跳过**

## 结果

| 配置 | recall@5 | recall@10 | MRR | 时延p50(ms) | 时延p95(ms) |
|---|---|---|---|---|---|
| baseline(全 20 例) | 0.314 | 0.329 | 0.256 | 500.5 | 801.0 |

分层:

| 层 | 例数 | recall@5 | recall@10 | MRR | p50 |
|---|---|---|---|---|---|
| **usage**(正式口径) | 8 | **0.750** | 0.750 | 0.504 | 523.9ms |
| reference(仅参考) | 12 | 0.023 | 0.049 | 0.090 | 466.4ms |

## 关键观察

**usage 层 0.750 与 reference 层 0.023 的巨大落差,不是检索质量问题,是两套 gold 定义的索引重叠度问题。**

- `usage` 层 gold = 真实使用产生的问题 + 人工读全文核实过的文档
- `reference` 层 gold = **官方 AI 的 recallDocuments**(官方认为相关的文档)

而官方向量库与匿名搜索接口**已知是两套索引**:实测某篇中文知识文档
(`873372977646105600`)能被官方 AI 召回,却不在匿名搜索前 50 条内(即使用
`BOM` 搜、带 `productIds=93` 过滤)。

因此用官方 AI 的 gold 去评匿名检索,低命中是**结构性必然**,测的不是检索质量。
报告脚本本身也已注明「reference 仅作参考,不作达标依据」。

**对决策的含义**:正式口径应只看 usage 层。当前 usage 层 recall@5=0.750(8 例,
样本仍偏小),与项目此前记录的「匿名上游 usage 层 0.756」一致,互为佐证。

## 复跑指引

放宽上游预算即可跑完:

```bash
# 服务需先起(仓根)
nohup python3 service/kingdee-ksearch-service.py 4097 &

# 完整基线(28 例 + 硬指标;上游消耗估算 100~120)
python3 scripts/run_eval.py --configs baseline --topk 4 \
  --max-requests 120 --sleep 1 \
  --json docs/eval-baseline-v6.1-full.json --out docs/eval-baseline-v6.1-full.md
```

注意:`--max-requests` 是**全轮共享**预算(搜索评测 + 硬指标共用),28 例含硬指标
需按上估放宽;本轮设 40 只够跑 20 例、硬指标全跳。
