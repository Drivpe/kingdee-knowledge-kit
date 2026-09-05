# 金蝶知识检索服务 API(v3.2 / 套件 v2.0)

逆向金蝶云社区官方后端,本地封装。**零账号、零点数、零凭据、零浏览器依赖**——检索/全文/问答包/分享对话全匿名;
合成回答两条路:`kd ai` 走你的模型通道,或调用方 AI 拿资料包自己写。

- 基址:`http://127.0.0.1:4097`(可用环境变量 `KSEARCH_URL` 指向其他实例)
- 启动:`scripts/start-service.ps1`(Windows)/ `scripts/start-service.sh`(*nix)
- 代码:`service/kingdee-ksearch-service.py`(纯标准库,无 pip 依赖)
- 自发现:`GET /manifest` 返回机器可读能力清单(端点/参数/实体/CLI 路径),agent 一次 GET 即会用

## kd CLI(v2.0,7 命令)

`search / read / ask / ai / share / manifest / health`。`read` 的 `--kind` 照抄 search 结果的 `type`
(`knowledge→/karticle`、`answer→/question`、`article→/article`;单条回答端点 `/answer` 仍可用但 CLI 不再单独暴露,
问答帖全文已覆盖)。AI-first 契约与 `kd ai` 的模型通道(`KAI_BASE` 勿带 `/v1`、`KAI_MODEL`)见根 README「进阶」。

## GET / 或 /manifest — 机器可读能力清单

```jsonc
{"service":"kingdee-ksearch","version":"3.2","anonymous":true,
 "cli":{"path":"<安装目录>/bin/kd.cmd|kd","commands":[...]},
 "endpoints":{...参数与返回说明...},
 "entities":{"knowledge":{"read":"/karticle"},"answer":{"read":"/question?id=<questionId>"},
             "article":{"read":"/article"}},
 "notes":["纯匿名","保持人类调用频率"]}
```

## POST|GET /search — 裸检索(三种实体全返回)

```jsonc
{"text": "信用额度控制", "productId": 93, "page": 1, "pageSize": 10,
 "global": false, "sortsType": 1, "type": null}
// GET: /search?text=信用额度控制&productId=93&pageSize=10&type=knowledge
```

| 参数 | 说明 |
|---|---|
| `text` | 关键词(必填)。具体功能名/业务名词/报错词 |
| `productId` | 93=星空旗舰版(默认) 87=苍穹 1=星空企业版/标准版;**`0`=不过滤(实现为省略参数——铁律:给上游传 `productIds[0]=0` 会当真值过滤,实测把 Knowledge 挤出前排)** |
| `pageSize` | ≤50;`page` 分页 |
| `global` | true=跨全部产品 |
| `type` | 可选过滤 `knowledge|answer|article`;过滤时自动跨上游页扫描凑满(`scanNote` 报告扫了几页) |

响应——三种实体全返回,`type` 字段区分:

```jsonc
{"ok":true, "total":31615, "results":[
  {"type":"knowledge","id":"<knowledgeId>","url":".../knowledge/<id>","title":"..","snippet":"..","products":[..],"views":..,"useful":..},
  {"type":"answer","id":"<answerId>","questionId":"<questionId>","url":".../question/<questionId>",
   "title":"问题标题","questionBody":"问题正文(前500字)","snippet":"回答摘要","adopted":false,"answersCount":4},
  {"type":"article","id":"<articleId>","url":".../article/<id>","title":"..","snippet":"..","supports":..}]}
```

## 全文与资料端点(POST|GET,均匿名)

| 端点 | 输入 | 返回 |
|---|---|---|
| `/karticle?id=` | knowledge 条目 id | 官方文档全文 `contentText` |
| `/question?id=` | answer 条目 questionId | 问题正文 + `answers[]`(采纳优先;前5条拉详情补全)+ `discussion[]` 追问链 |
| `/answer?id=` | answer 条目 id | 单条回答全文 |
| `/article?id=` | article 条目 id | 社区文章全文 |
| `/ask` | `{"text"}` 或 `{"keywords":[..]}` + `{"productId"?,"topK"=4}` | 一站式资料包:检索(keywords 模式多词合并去重)+按相关度深读 topK 全文;**调用方 AI 据此合成带引用回答** |
| `/share` | `{"link":"官方分享短链 \| /searchchats/{id} \| chatId"}` | 官方 AI 分享对话全文:`chats[]{question,answer(Markdown),refs[](title/url/summary/entityType)}` |

## 上游匿名接口(逆向铁证,均实测 200 无 cookie)

```
GET  https://vip.kingdee.com/api/search?text=&page=&pageSize=&global=&sortsType=&productIds[0]=
GET  https://vip.kingdee.com/knowledgeapi/knowledge/{knowledgeId}
GET  https://vip.kingdee.com/api/questions/{questionId}
GET  https://vip.kingdee.com/api/questions/{questionId}/answers?page=&pageSize=
GET  https://vip.kingdee.com/api/answers/{answerId}
GET  https://vip.kingdee.com/api/articles/{articleId}
GET  https://vip.kingdee.com/aisapi/ai-search/sharing-chats/{chatId}   # 分享对话;键是 chatId 非 sharingId
```

注意:网页 /question/{id} /knowledge/{id} 有登录门(302),底层 API 不校验——本服务只走 API。
回答列表条目正文在 `summary` 字段(可能截断),详情 `description` 更全;`appendQuestionsAndAnswers` 是免费附带的追问对话链。

## 字段要点

- `/api/questions/{qid}/answers` 列表条目正文在 `summary`(纯文本,可能截断);`/api/answers/{id}` 详情正文在 `description`(HTML,最全)
- 官方分享回答的引用在 `recallDocuments[]`(title/summary/url/entityType),entityType 与本服务检索的三种实体同源
