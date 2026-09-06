# kd 检索 A/B 评测报告

评测集: evalset.json(26 例,usage=正式金标 12 例 / reference=官方对话参考 14 例) | RRF k=60 | 生成时间 2026-09-06 11:48:45

≥ 正式达标口径=usage 层;reference(官方对话金标)仅作参考,不作达标依据。

| 配置 | recall@5 | recall@10 | MRR | 时延p50(ms) | 时延p95(ms) | 缓存命中 |
|---|---|---|---|---|---|---|
| cache | 0.413 | 0.441 | 0.392 | 12.3 | 20.8 | 54 |

### 分层(正式口径 = usage 层)

- **cache · usage(12例)**: recall@5=0.756 recall@10=0.800 MRR=0.628 p50=12.4ms
- **cache · reference(14例)**: recall@5=0.120 recall@10=0.134 MRR=0.190 p50=12.3ms
