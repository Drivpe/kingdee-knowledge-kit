# 票 #22:`kd search` 升级为多路拆词 · 只出标题清单

状态: ✅ 完成(2026-09-29 校正) | 依赖:无 | 优先级:P1
验收依据: 用例 `t_gold_error_code_case`(固定实证:目标文档 id `646787188905978624` 必在清单内且标题含「错误码」),见 docs/specs/2026-09-29-验收记录.md
实现沿革: 本票的多路清单执行链**实现于 v6.4/v6.6 演进**(载体 `src/kd/_impl/_manifest.py`),非 2026-09-29 本轮新增
spec: `docs/specs/2026-09-17-search多路清单化.md`（**实现前先通读**；第 4 节五个未决项已裁决，勿重开）

## 问题

`ask` 的 RRF 融合（`1/(60+名次)` 求和）在**报错码场景**上被实测证伪。检索词 `应用为禁用状态[网关]`、产品线 93、同一时刻对照：

| 路径 | 目标文档排名 |
|---|---|
| `search`（上游原生序） | 第 5 ✅ |
| `ask --topk 4`（RRF 后） | **第 8** ❌ 在 topK 外 |

根因：融合**奖励"多路都提到"的泛泛文档**（四路各第 20 名得 `0.05`），**惩罚"单路精确命中"的目标文档**（原句路第 1 名只得 `0.0164`）。且融合抹掉了"哪一路命中"这个调用方最需要的信息。

## 改动

按 spec 实现，四个已定设计（多路+清单 / 丢 RRF / 不返正文 / 每路 10 条）+ 五个未决项裁决：

1. `_plan_routes` 接入 `search` 执行链，每路各发一次上游检索。
2. 去重键统一为 `(type, id)`（`questionId` 仅展示）；**修掉三处键不一致**（见 spec「去重键」节）。
3. 排序：命中路数降序 → 同路数按上游原生序。**不使用 `_rrf_fuse`**。
4. 输出：`type` / `id` / `title` / `hitRoutes` / `routes[]`，**不返回 `contentText`**。
5. 新增参数 `--routes N`（默认 7，取自 `query_routes.json` 的 `maxRoutes`）；**不引入 `--multi` 开关**。
6. `type_` 过滤每路都带、每路独立跨页扫描。
7. 预算：新增 `budget.maxUpstreamPerSearch`（`query_routes.json`，默认 24）。
8. `page`/`page_size` 重新定义为**清单分页**（破坏性语义变更，写进 commit message）。
9. 新增 `routeErrors[]`：单路失败不拖垮整轮，但**必须暴露**，避免被读成"官方没这类文档"。

## 必修的解析坑（spec 3.3 / 本票验收项）

**先复现再修**：

```bash
python3 src/kd_run.py search "应用为禁用状态[网关]" --product 93 --size 10
```

观察 answer 条目（第 3、4 条）`title` 是否为空。`_norm_item` 对 answer 取值顺序是 `hl.get("question.title") or q.get("title") or ""`——需核实上游 answer 条目的 `highlight` 里**是否真有含点号的 `question.title` 键**，以及为何同一条在不同调用路径下表现不同。

**此坑不修不算完成**：多路清单的核心交付物就是标题，标题缺失直接摧毁可用性。

## 守卫同步（漏改即 FAIL）—— 已核原文，按此改

**① `scripts/check_core_surface.py:48-54`**，`search` 项当前为：

```python
"search": "(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1, "
          "type_=None, rerank=None, budget=None, rate=None)",
```

改为（新增 `routes=None`，位置在 `rate` 之前；**逐字比对，差一个空格即 FAIL**）：

```python
"search": "(text, product_id=None, page=1, page_size=10, global_=False, sorts_type=1, "
          "type_=None, rerank=None, budget=None, rate=None, routes=None)",
```

**② `tests/kd_regression.py:86-91`**，白名单当前为：

```python
SEARCH_KEYS = {"ok", "text", "total", "queries", "page", "pageSize", "totalPages",
               "results", "scanNote", "stats"}
RESULT_BASE_KEYS = {"type", "id", "url", "title", "snippet", "products", "views",
                    "useful", "contentLen", "updatedAt"}
RESULT_ALLOWED_KEYS = (RESULT_BASE_KEYS - {"useful"}) | {
    "useful", "supports", "questionId", "questionBody", "adopted", "answersCount", "comments"}
```

⚠️ **这里是硬阻断，不是"记得改"**：第 327-328 行同时调用 `check_subset` **和 `check_no_extra`**：

```python
check_subset(ks, SEARCH_KEYS, "search 顶层")
check_no_extra(ks, SEARCH_KEYS | {"text"}, "search 顶层")
```

**`check_no_extra` 禁止任何多余键**——只要输出里出现 `hitRoutes` / `routes` / `routeErrors` 而白名单没加，用例立即 FAIL。结果项（第 350-351 行）同理。

改动：
- `SEARCH_KEYS` 增 `routeErrors`（顶层新增字段；`routes` 顶层已由 `queries` 近似承载，若确需顶层 `routes[]` 明细则一并加）。
- `RESULT_ALLOWED_KEYS` 增 `hitRoutes`、`routes`。

**③ `SIGNATURE_BASELINE` 的 `ask` / `read` 项不动**（本票不改 `ask`）。

**④ `_rrf_fuse` 本轮不删**（`ask` 仍在用），`FORBIDDEN_NAMES` 判据自然 PASS。

**⑤ 公开面仍是 `ask`/`search`/`read` + 三个异常类**（判据 1/3/4/6）。

**⑥ CLI 帮助文案必须同步改**（`src/kd/cli.py:174-176`）

`search` 子命令当前的 `help` 与 `epilog` 原文是：

```python
s = sub.add_parser("search", help="手动细粒度调试命令:检索知识库(常规问题请用 kd ask;三种实体全返回)",
                   epilog='示例: kd search "信用额度控制" --product 93 --type answer',
```

⚠️ **"常规问题请用 kd ask" 与本轮改造目标直接矛盾**——本票正是让 `search` 取代 `ask` 成为常规入口。这句不改，CLI 自述与设计相反，用户/agent 会被误导回 `ask`。

改为反映新定位（`search` = 常规入口，出清单；`ask` = 一站式资料包，次要）。

**`read` 子命令的 help 也有同一问题**（`src/kd/cli.py:193` 附近）：

```python
s = sub.add_parser("read", help="手动细粒度调试命令:读全文(常规问题请用 kd ask);"
```

同样把 `ask` 定位成常规入口，须一并改为"先用 `kd search` 出清单，再 `kd read` 取全文"的新流程。

`ask` 子命令自身的 help 若含"常规问题请用 X"表述也一并核对。

## 新增回归用例（spec「Testing Decisions」）

1. 多路去重：同一 answer 被两路命中 → 清单只一条、`hitRoutes=2`。
2. answer 双 id 空间：同一帖**不同回答不被合并**。
3. `routeErrors`：某路 `UpstreamError` → 被记录、其余路结果仍返回。
4. 预算耗尽：`budget_exhausted=true` + `scanNote` 反映实际完成路数。
5. **固定实证用例**：`应用为禁用状态[网关]` / 产品线 93 → 断言《金蝶AIOpenAPI错误码说明》（id `646787188905978624`）出现在清单里。**这是本票存在的唯一理由，必须由测试守住。**

## 验收

```bash
python3 tests/kd_regression.py                 # 10/10, EXIT=0
python3 scripts/check_core_surface.py          # 10 判据全 PASS, EXIT=0
python3 tests/kd_regression.py --online        # 29/29, EXIT=0
```

外加：
- `python3 src/kd_run.py search "应用为禁用状态[网关]" --product 93` → 目标文档在清单前列，每条带 `hitRoutes`，answer 条目 `title` 非空。
- 上游请求数在 `budget.maxUpstreamPerSearch` 内；`stats.upstreamCalls` 与路数一致。
- 提交信息按 `de0afea` / `4e297e6` 风格：每项写清「问题 → 实证 → 修法 → 验收」。

## 不做（spec Out of Scope）

- **不改 `ask`**（裁决⑤：它是另一个调用面，本轮一行不动）。
- 不改 `query_routes.json` 的**拆解规则内容**（本轮只改执行链）。
- 不做图片抽取（另见图片调研文档）。
- `git push` 不可用（GitHub 封禁未解），提交即止。
