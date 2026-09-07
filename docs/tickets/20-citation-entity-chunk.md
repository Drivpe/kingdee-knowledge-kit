# 票 #20:引用形态改造 + kd read --chunk(ADR-0007)

状态:主体完成(剩余小项见下,批次 2 处理) | 依赖:无 | 优先级:P1

## 改动
按 ADR-0007 落地:①kd read 新增 --chunk <id>(匿名 GET /aisapi/document-chunks/{id});②落地缓存 front-matter 增 entityId;③输出端 URL 静态标 link_public:false(前缀判定,禁止逐条探测);④ANSWER-SPEC 引用格式改「标题 + entityId + chunkId」。

### 完成项
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

## 剩余小项(批次 2 处理,本次不做:会与并行代理冲突/需重启服务)
- ② 落地缓存 front-matter 增 entityId(需改 service/ 落地缓存写入逻辑——票 #18 并行代理正在改 service/,避免冲突;且可能需重启服务);
- ③ 输出端 URL 静态标 link_public:false(前缀判定,零上游请求,禁止逐条探测——同样落在服务端输出层)。

## 纪律
- 本次真实上游请求仅 2 个(2659901、2659902),间隔 ≥3 秒;chunkId 不可枚举,勿批量。
- 未改 service/ 任何文件;未重启服务;未 git 提交。
