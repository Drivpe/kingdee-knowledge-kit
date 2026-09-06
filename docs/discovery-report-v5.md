# v5 发现层首轮报告(corpus 语料 + 时间网格)

日期:2026-09-06 | 环境:生产 4097(v5.0),corpus=`~/.lingeebuild/corpus` | 依据:交接文档 17、ADR-0004

## 首轮时间网格

- 词表 `data/discovery-terms.json`(42 词:usage 12 + eval 12 + domain 18)× sortsType=2(时间倒序)× 前 3 页(pageSize=25);
- 频率红线:1 请求/秒,实际上游请求 ≈126(≤200 上限,部分词提前收工于 totalPages);
- 结果:**corpus 落盘 1209 篇**(knowledge 143 / answer 802 / article 264;stub=标题+摘要,正文按需深读写穿覆盖),
  其中时间网格新增 stub 1051 篇,read/ask 写穿回填 157 篇(usage/邻域来源)。

## 金标覆盖率(corpus 对评测集金标文档的命中)

| 层 | 发现前 | 一轮后发现 |
|---|---|---|
| usage(23 篇金标) | 0% | 2/23 = 8.7% |
| reference(89 篇金标) | 0% | 15/89 = 16.9% |

- usage 层命中的金标:《计划订单的分子分母与bom不一致的原因与方案》(knowledge/828071402723646720,discovered_by=timesweep)——
  **恰好是 use-bom-27000 / exp-plan-bom-ratio 两例的解题文档**;
- 覆盖率随发现轮次与真实使用(usage 沉淀)持续生长;reference 层剩余缺口与搜索索引不覆盖的文档一致(实证报告 §6)。

## rg 冒烟(关上游场景,验收 §5.1)

零上游调用,纯 rg 检索 corpus:

```
$ rg -il "分子分母" ~/.lingeebuild/corpus
→ knowledge/828071402723646720.md(= use-bom-27000 的解题文档)
```

一道历史 usage 问题仅靠本地语料即可定位解题文档 ✓(front-matter 带原链接,回答可直接引用)。

## 在线管线透明性(验收 §5.2)

发现前后 `run_eval --configs cache` 逐位一致(cache 0.413/0.441/MRR 0.392;usage 0.756/0.800/0.628)——
发现层只向 corpus 供给语料,不扰动在线检索管线(设计如此;基线见 `docs/eval-baseline-v5.md`,对照 `docs/eval-postdiscovery-v5.md`)。

## 复现

```bash
python scripts/discovery_sweep.py --dry-run          # 预览请求计划
python scripts/discovery_sweep.py                    # 全词表 3 页,1 req/s,≤200 请求
python tests/verify_ksearch.py                       # 回归(31 项,含 v5 corpus 检查)
```
