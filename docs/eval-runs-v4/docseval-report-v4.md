# kd 检索 A/B 评测报告

评测集: evalset.json(26 例,usage=正式金标 12 例 / reference=官方对话参考 14 例) | RRF k=60 | 生成时间 2026-09-06 00:55:15

≥ 正式达标口径=usage 层;reference(官方对话金标)仅作参考,不作达标依据。

| 配置 | recall@5 | recall@10 | MRR | 时延p50(ms) | 时延p95(ms) | 缓存命中 |
|---|---|---|---|---|---|---|
| baseline | 0.413 | 0.441 | 0.392 | 426.5 | 516.2 | 0 |
| cache | 0.413 | 0.441 | 0.392 | 13.4 | 20.3 | 54 |

### 分层(正式口径 = usage 层)

- **baseline · usage(12例)**: recall@5=0.756 recall@10=0.800 MRR=0.628 p50=421.3ms
- **baseline · reference(14例)**: recall@5=0.120 recall@10=0.134 MRR=0.190 p50=439.5ms
- **cache · usage(12例)**: recall@5=0.756 recall@10=0.800 MRR=0.628 p50=13.3ms
- **cache · reference(14例)**: recall@5=0.120 recall@10=0.134 MRR=0.190 p50=13.4ms

cache 相对 baseline:p50 -97%,p95 -96%(负数=更快),缓存命中 54 次检索
