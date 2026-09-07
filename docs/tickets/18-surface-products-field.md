# 票 #18:结果体透传产品线(products 字段上浮)

状态:✅ 完成(2026-09-07) | 依赖:无 | 优先级:P1

## 问题
上游每条结果自带产品线字段(answer 详情 products 数组、knowledge 同),但 ask 的 sources 顶层不透传(service:943-948),agent 甄别需翻 detail。

## 改动
ask 资料包每源附 products(及生效 productId 回显);search/read 同步。

## 实际改动(仅 service/kingdee-ksearch-service.py,已同步部署副本)
- `ask_bundle()` sources 组装:每个 source 顶层新增 `"products"`(票 #18)——detail 优先(`d.get("products")`),缺省回退检索条目 `item.get("products")`(_norm_item 三实体均有),再缺省 `[]`。
- `ask_bundle()` 返回体顶层新增 `"effectiveProductId": product_id`(票 #18)——回显 plan_routes 实际生效的产品过滤。
- `ask_bundle()` note 末尾追加一句字段说明,供调用方 AI 直读。
- `kd search`(knowledge_search→_norm_item)与 `kd read`(detail 各函数)原本就携带 products 且 CLI 仅透传服务 JSON,确认未丢弃,**未改 cli/ 与其他文件**。

## 验证证据(2026-09-07 实测)
1. `py_compile` 仓库版通过;cp 同步 `~\.lingeebuild\config\kingdee-ksearch-service.py` 后编译通过。
2. 重启服务,`kd health` → `kingdee-ksearch v6.1`,anonymous=true 正常。
3. `kd ask "BOM维护" --kw "BOM维护" --product 93 --topk 2`(缓存命中,上游 0 请求):
   - 顶层:`"effectiveProductId": 93`
   - routes:`[{"kind": "explicit", "terms": "BOM维护", "productIds": 93}]`
   - source(rank1, knowledge 272106361545148416):`"products": ["金蝶AI套件"]`,与 detail.products 一致
   - source(rank2, knowledge 584330946073958912):`"products": ["金蝶AI套件"]`
4. `kd ask "生产订单下推" --product 93 --topk 2`(非缓存词,走真实上游):
   - `effectiveProductId: 93`;routes:`[{"kind": "product", "terms": "生产订单", "productIds": 93}]`
   - source1(answer):`products=["金蝶AI套件","管理会计"]`;source2(knowledge):`products=["金蝶AI套件"]`;均与 detail.products 一致
   - 注:该词 answer 回答展开较贵,实际消耗上游 7 请求,略超 ≤6 预算(回答翻页+逐条详情所致);后续验证全部缓存命中零上游。
5. `kd search "生产订单" --product 93 --type knowledge --size 25`:每个条目顶层 `products: ["金蝶AI套件"]`(既有字段,透传正常)。
6. `kd read 272106361545148416`(缓存):detail 顶层 `products: ["金蝶AI套件"]`,CLI 未丢弃。

## 遗留
- 无。产品线甄别现在可从 `sources[].products` 顶层直读,无需翻 detail;`effectiveProductId` 可核对本次过滤未串线(防 2026-09-06 企业版当旗舰版类事故)。
