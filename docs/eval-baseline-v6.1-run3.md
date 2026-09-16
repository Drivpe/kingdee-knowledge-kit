# kd 检索基线评测(修正轮,2026-09-16)

> 本文件是**当前正式口径**。取代 `eval-baseline-v6.1-full.md`(product 字段缺陷版)。
> 修正内容:评测集 7 个用例的 `product=0` → 实测归属产品线。
> 分析详见私有仓 `docs/research/2026-09-16-评测可复现性与product字段缺陷.md`。

## 运行条件

- 评测集:`data/eval/evalset.json`(池 28 例;usage=正式金标 14 例 / reference=官方对话 14 例)
- 配置:`baseline`(服务 v6.1,多路 ask、`rerank=false`、`cache=false`)
- 参数:`--topk 4 --max-requests 120 --sleep 1`
- 结果:28/28 例完成,上游消耗 72/120,硬指标 1/1 例完成
- 产物:`docs/eval-baseline-v6.1-run3.json`

## 结果

| 配置 | recall@5 | recall@10 | MRR | 时延p50(ms) | 时延p95(ms) | 缓存命中 |
|---|---|---|---|---|---|---|
| baseline(全 28 例) | **0.488** | **0.533** | **0.462** | 519.8 | 666.2 | 0 |

分层:

| 层 | 例数 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| **usage**(正式口径) | 14 | **0.814** | 0.852 | 0.669 |
| reference(仅参考) | 14 | 0.163 | 0.213 | 0.256 |

## 与修正前的对比

| | 修正前 | 修正后 | 变化 |
|---|---|---|---|
| 全池 recall@5 | 0.396 | **0.488** | +0.092 |
| 全池 recall@10 | 0.426 | **0.533** | +0.107 |
| 全池 MRR | 0.373 | **0.462** | +0.089 |
| **usage 层 recall@5** | 0.671 | **0.814** | **+0.143** |
| reference 层 recall@5 | 0.120 | 0.163 | +0.043 |

改善的 4 个用例(此前均为**假失败**):

| 用例 | 修正前 | 修正后 | 根因 |
|---|---|---|---|
| exp-bom-molecule-limit | 0.00 | **1.00** | product 应为 93 |
| exp-production-unit-qty | 0.00 | **1.00** | product 应为 93 |
| off-mc-download | 0.00 | 0.40 | product 应为 93 |
| off-mc-version-view | 0.00 | 0.20 | product 应为 93 |

**机制**:`product=0` 时检索不带产品过滤,候选池从上万个文档中检索,目标被噪声淹没。
实测同一用例 `productId=0` → r5=0.00(候选池 1122);`productId=93` → r5=1.00(候选池 131)。

## 已修正的用例

| 用例 | product | 依据 |
|---|---|---|
| off-mc-version-view | 93 | gold 文档 `products[].id` 实测 |
| off-mc-patch-missing | 93 | 同上 |
| off-mc-download | 93 | 同上 |
| exp-bom-molecule-limit | 93 | 同上 |
| exp-production-unit-qty | 93 | 同上 |
| exp-plan-bom-ratio | 1 | gold 属企业版/标准版 |
| exp-jump-layer-ratio | 1 | 同上 |

## ⚠️ 仍未解决:23% 的 gold 是失效 ID

核验全池 120 个 gold,发现 **28 个(23%)返回 `errorCode:404`**,涉及 9 个用例。

**4 个用例的 gold 全部失效**(零命中无意义,应从分母剔除):

| 用例 | 失效 gold |
|---|---|
| off-mc-exclusive | 5/5 |
| off-mc-exclusive-persist | 5/5 |
| off-currency-bind | 8/8 |
| off-price-scene | 5/5 |

**因此上表数字仍含失效 gold 的稀释,清理后才算最终口径。**
特别是 reference 层的 0.163 被这 4 个用例系统性拉低。

另有一处 gold 的 `type` 标错:`exp-mrp-demand-error` 的 gold
`{"type":"knowledge","id":"782292679064870912"}` 实为 **question ID**
(搜索结果中作为 `type=answer` 条目的 `questionId` 命中),其 r5=1.00 是真实命中。

## 硬指标

| 指标 | 值 | 样本量 |
|---|---|---|
| 词汇鸿沟存活率 | 1.000 | 1/1 例 |
| 根因可解释率 | 1.000 | 1/1 例 |

命中用例:`seed-vocab-gap-bom-27000`,gold `873372977646105600` 进 sources,
chunk 命中关键词 `生产单位数量 / 隐藏字段 / 自动除以`。

**n=1 是池设计缺陷**,放宽上游预算不改变该数字 —— 全池仅 1 例带 `hardMetric` 标签。

## 唯一确认的真检索失败

`use-bom-designer-page-2026-09-06`:`product` 已是 93、gold 有效、
`productId` 0 与 93 两种参数下均零命中。**此例与数据缺陷无关,是真实的检索失败。**

其余低分用例(`exp-skip-layer-mrp` 0.67、`use-bom-27000` 0.40、
`seed-vocab-gap-bom-27000` 0.33)需在清理失效 gold 后重新归因。

## 复跑指引

```bash
# 服务需先起(仓根)
nohup python3 service/kingdee-ksearch-service.py 4097 &

python3 scripts/run_eval.py --configs baseline --topk 4 \
  --max-requests 120 --sleep 1 \
  --json docs/eval-baseline-v6.1-run3.json --out docs/eval-baseline-v6.1-run3.md
```

`run_eval.py` 已加 `product=0` 告警,防止再次静默退化为全域检索。
