# ADR-0007:引用形态改为「标题 + entityId + chunkId」,废除网页 URL 引用

日期:2026-09-06 | 状态:已采纳(票 #20 拍板立项) | 关联:ADR-0005(引用粒度词条继承)、docs/research/2026-09-06-official-ai-frontend-recon.md、docs/research/2026-09-06-product-routing-and-link-availability.md

## 背景

2026-09-06 真实使用事故:agent 按 kd 检索结果引用了 4 条 `vip.kingdee.com/question|article/{id}` 链接,用户点击全部 404——实测这些网页路由匿名一律 302 到 passport 登录墙。kd 检索返回的 URL 只证明"检索系统里有这条记录",不证明"匿名浏览器可访问";引用未经可访问性验证是流程缺口。

同日对官方 AI 的侦察给出了它的解法:**官方从不引用网页 URL**。其引用 = `entityId + entityType + chunkId(+title)`,chunk 全文经 `GET /aisapi/document-chunks/{id}` 匿名解析(body 级实测 200,零 cookie);仅 knowledge 类附带网页 URL 但不作溯源依赖。

## 决策

1. **引用标准形态**:`标题 + entityId + chunkId`;需要全文时匿名调 `document-chunks/{chunkId}` 实时解析。网页 URL 永不作溯源依赖,输出时必须静态标注 `link_public: false`(按 URL 前缀判定,零上游请求,禁止逐条 HEAD 探测——频率红线)。
2. **新能力 `kd read --chunk <id>`**:手动细粒度入口,按 chunkId 匿名拉官方 chunk 全文(含 entityId/entityType/title/documentId 映射)。
3. **落地缓存 front-matter 增补** `entityId` 字段(已有 url 字段保留作记录,但降为非溯源依赖)。
4. **ANSWER-SPEC 同步**:引用格式、`link_public` 标注规则、同批写入「product 路由粘性 + 引用前核对 products 字段」硬规则(票 #19)。

## 后果

- 引用永不 404(匿名可解析);agent 离线时也可凭 entityId+chunkId 重取原文;
- 产出消费方(人/agent)多一步"要全文再拉 chunk"的动作,换来可验证性;
- 官方 chunkId 不可枚举,只能从官方 AI 回答/分享链收集——chunk 溯源是"引用出现时"的能力,不是批量能力;
- 上游页面路由变更不影响溯源(chunk 接口才是依赖面),上游接口变更才影响——依赖面收窄且更稳。

## 备选与取舍

- **引用前逐条 HEAD 校验**:被否——打上游频率红线,且"假 200"(状态 200 body 401)让状态码校验本身不可靠;
- **只标"需登录"不改形态**:被否——用户拿到的引用仍不可用,问题只换了个说法;
- **全面切到分享短链**:被否——短链落地页匿名同样登录墙(实测),且依赖官方对话的存在性。

## 引用来源

- 实测:四类网页 URL 匿名 302→passport;`/aisapi/document-chunks/2659901` 匿名 200 含全文与映射;
- bundle 常量表:SEARCH_CHRUNKS_DETAIL 端点;官方分享对话 refs 结构(entityId/entityType/chunk id);
- 事故复盘:docs/research/2026-09-06-product-routing-and-link-availability.md 缺陷 2。
