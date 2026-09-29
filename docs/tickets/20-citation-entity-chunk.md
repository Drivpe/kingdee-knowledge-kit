# 票 #20:引用形态改造 + kd read --chunk(ADR-0007)

状态:**✅ 已关闭(2026-09-29 台账校正)** —— 四个子项全部失效,本票不再有可做工作:
① 曾真实落地,但已于 2026-09-28 随 ADR-0016 决策 3 **主动删除**;②③ **从未实现,且其实现载体**
(落地缓存 / 服务端输出层)随 ADR-0011 去服务化**整体删除** —— 属"确实没做,且不再可达";
④ 曾被标完成,但已被 ADR-0012 决策 3 + ADR-0007「后续更正」**推翻**(现行口径反而要求给网页链接)。
详见下文「台账校正」。 | 依赖:无 | 优先级:P1(已关闭)

## 改动
按 ADR-0007 落地:①kd read 新增 --chunk <id>(匿名 GET /aisapi/document-chunks/{id});②落地缓存 front-matter 增 entityId;③输出端 URL 静态标 link_public:false(前缀判定,禁止逐条探测);④ANSWER-SPEC 引用格式改「标题 + entityId + chunkId」。

### 完成项(原文保留;①④ 现已失效,见文末「台账校正」)
- ✅ ① kd read --chunk(cli/kd.py):
  - `kd read <chunkId> --chunk` 匿名直连 `GET https://vip.kingdee.com/aisapi/document-chunks/{chunkId}`(CLI 层直连上游,不走 4097 服务——**有意取舍:v6.2 应收编进检索服务(唯一事实源),当前为避免与票 #18 改服务文件冲突而暂放 CLI**,已注明于代码注释);
  - 实现要点:UA 伪装(与 _chat 同理,防 Cloudflare 403);错误均出带 hint 的错误 JSON(bad_chunk_id / chunk_not_found(404) / upstream_http_* / upstream_unreachable / chunk_bad_payload——防「假 200」以 content 字段为准,ADR-0007 备选取舍);stdout 纪律保持(只出 JSON,退出码 0/1);
  - 证据:实跑 `kd read 2659901 --chunk` 返回 200 JSON,含 content(块全文)/documentId/entityId("873372977646105600")/entityType("Knowledge")/id/title 六字段,退出码 0;实跑 `kd read 1 --chunk` 返回 error JSON(code=bad_chunk_id,带 hint 与 example),退出码 1;
  - 附带验证:`kd read 2659902 --chunk` 亦返回完整块全文(实体 873372985782736128)——上游匿名可用性二次确认;
  - 遗留:上游 404 分支(chunk_not_found)未经真实请求验证(两次真实上游请求预算内均为可用 chunk,且本地格式守卫先行拦截了非法 id),代码审查级确认,错误形态与 `_http` 一致。
- ✅ ④ ANSWER-SPEC 引用格式(docs/ANSWER-SPEC.md,v2.0→v2.1,第 3 条):
  - 引用标准形态 = `标题 + entityId + chunkId`,需要全文时匿名调 `kd read <chunkId> --chunk` 解析;
  - 网页 URL 只作记录且必须标注「(需登录)」,禁止作为溯源依赖(匿名一律 302 登录墙)。

## 验收
任取一分享对话的引用,kd read --chunk 能匿名还原全文;answers 引用样例零网页 URL 依赖。

- ✅ kd read --chunk 匿名还原全文:见上 ① 证据(chunk 2659901/2659902 均含块全文与 entityId 映射)。
- ⏳ answers 引用样例零网页 URL 依赖:依赖 ③(输出端标注)落地后在回答样例中复核——批次 2。

## 剩余小项(原文保留)——2026-09-29 核实下场

> 以下两条原文照录(要点完整保留)。核实结论:**这两项从未被实现过**,而它们当年的
> 待写目标(`service/` 的落地缓存写入逻辑、服务端输出层)又已随 **ADR-0011 去服务化**整体删除。
> 故它们**既不是「已完成」,也不是「待做」**——是「**确实没做 + 已不再可达**」。这是本票关闭的理由。

- ② 落地缓存 front-matter 增 entityId(需改 service/ 落地缓存写入逻辑——票 #18 并行代理正在改 service/,避免冲突;且可能需重启服务);
  - **下场:从未落地,且载体已随 ADR-0011 去服务化删除 —— 双重失效。**
    **(a) 从未落地(有反证)**:删除前落地缓存的 front-matter 写入实现是 `service/docstore.py`,
    其字段集在模块 docstring 中逐字列明为 **`id / type / url / title / updatedAt`**
    (`git show 6afa727^:service/docstore.py:12-16`),**不含 `entityId`**;
    删除前 `service/kingdee-ksearch-service.py` 里 `entityId` 仅出现在 **`_corpus_write`**
    (官方 AI 分享对话引用 → **corpus** stub,`git show 6afa727^:...:1100/1105`),
    那是 **corpus 语料目录**,**不是 landing 落地缓存**。故 ② 这项**从未被实现过**——
    它与「批次 2 待办」的措辞一致,属**确实未做**。
    **(b) 载体已删除**:`service/` 整目录删除(`6afa727`,2026-09-17);
    ADR-0011 决策 3 明示 **「`corpus/`、`~/.lingeebuild/landing`、`data/ksearch.db` 三处缓存全部取消」**
    (`docs/adr/0011-decommission-http-service.md:60-66`)。
    **两个待写目标(corpus 与 landing)同时消失**,故该待办**既未完成也不再可达**。
  - 代码留痕:`src/kd/_impl/_config.py:78` 「内核不落盘(ADR-0011 决策 3):落地缓存与包内日志写盘整体摘除」;
    `src/kd/_impl/_detail.py:210` 「详情统一入口(纯在线,**不写穿落地缓存**)」;
    `src/kd/cli.py:155` `"noDiskWrite": True`。
  - 旁证:`src/kd/` 全目录 rg `entityId|entity_id` **零命中**——现行内核里该字段既无生产者也无消费者。
- ③ 输出端 URL 静态标 link_public:false(前缀判定,零上游请求,禁止逐条探测——同样落在服务端输出层)。
  - **下场:从未落地(连删除前的 service 里都零命中),载体已删除;其意图被 `linkPolicy` 取代并落地为活代码。**
    **全仓(含删除前历史)**rg `link_public` **仅 2 处**,且**全在文档**:
    `docs/adr/0007:13` 与 `docs/adr/0007:42`(后者是**宣告它作废**),加上本票自身;
    `git show 6afa727^:service/kingdee-ksearch-service.py | grep link_public` → **零命中**。
    即这个键**从未进入过任何实现**,连被删掉的 service 里也从未有过。
    它当年的意图「**是否把该 url 交给读者,由静态政策决定而非逐条探测**」已由等价且更完整的机制接管:
    `contract.json` 的 `linkPolicy`(`src/kd/contract.json:99-122`,status `settled`,`rule` 四档)
    → 生产消费者 `apply_link_policy()`(`src/kd/_impl/_config.py:265-281`,自述「本声明**在生产路径上的唯一生效点**」)
    → 调用面:`_config.link_policy()` (:245-257)、`_config.link_for()` (:260-262)。
    **判定:`link_public:false` 从未实现;其语义(静态政策定链接可见性、零探测)已由 `linkPolicy` 等价实现**
    —— 且方向已翻转:现行政策三档给链接(`knowledge`/`article`/`question`),而 ③ 当年设想的是「统一标 false」。
    另证:`docs/adr/0007:43`(「后续更正」)明示决策 3 的「`link_public: false` 静态标注」**已作废**。

## 台账校正(2026-09-29,逐项复核)

- ① 曾**真实落地**并留有实跑证据(见上「完成项」),但已于 **2026-09-28 被 ADR-0016 决策 3 主动删除**:
  `docs/adr/0016-检索词生成权移交调用层.md:54` —— 「删 `--chunk` | 解析后立刻报错的空参数,连帮助文本一并删
  (与 `ask` 同处理:「一律真正删除,不留 unsupported 占位」)」;代码留痕 `src/kd/cli.py:16`;
  删除提交 `6d3c493`(`git log -S'--chunk' -- src/`)。ADR-0007 后续更正在 `docs/adr/0007:43` 亦已明示
  「`kd read --chunk` 消费端**已随去服务化删除**」。故 ① 由「✅ 完成」变为 **曾完成、现已移除**。
  **本票标题中的 `kd read --chunk` 已不再存在,标题仅为历史留痕。**
- ②③ **从未实现**(见上「剩余小项」的逐条反证:删除前 `docstore.py` 的 front-matter 字段集不含 `entityId`;
  `link_public` 在删除前的 service 里零命中),且其待写载体随 ADR-0011 整目录删除,
  故**既不计为已完成,也不计为待做**——按本仓硬纪律,不保留**不可达**的待办项,故关闭本票。
- ④ 曾被标 ✅,但**已被 ADR-0012 决策 3 推翻**:引用形态改为可点击角标,而**非** `entityId + chunkId`
  (`docs/adr/0012-ask-default-product-and-answer-format.md:158`);`docs/adr/0007:42` 同述。
  `docs/ANSWER-SPEC.md:25-26` 现行口径为「三类都给链接」,**零 `entityId`**。
- 验收第 2 条「⏳ answers 引用样例零网页 URL 依赖」当年**从未完成**,且其前提(③)已消失——
  现行 ANSWER-SPEC **要求**给网页链接(`docs/ANSWER-SPEC.md:25-26`),该验收项**已被反向取代**,不再成立。
- 纪律节的历史陈述(2 次上游请求 / 未改 service/ / 未 git 提交)属**当时事实**,保留不改。

## 纪律(历史记录,2026-09-06 当次)
- 本次真实上游请求仅 2 个(2659901、2659902),间隔 ≥3 秒;chunkId 不可枚举,勿批量。
- 未改 service/ 任何文件;未重启服务;未 git 提交。
