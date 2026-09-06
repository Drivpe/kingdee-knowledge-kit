# 调研:全站语料落盘有没有比「关键词×50页×去重」更便宜的批量通道?(2026-09-06)

> 起因:计划把 vip.kingdee.com 全站文档「标题+摘要」批量落盘为本地语料;按关键词空间切片枚举估算 100-150 查询 × 50 页 ≈ 最多 7500 次请求,成本过高。
> 方法:一手来源 = 本次直接实测(robots.txt / sitemap.xml / 各域名探测 / DokuWiki 索引),全程金蝶域名共 **10 次请求、间隔≥1s、零登录零 Cookie**;二手 = WebSearch。每条结论标注证据等级;没找到的写明搜索词与结果。

## 0. 结论表

| # | 问题 | 结论 | 等级 |
|---|---|---|---|
| 1a | 全量 sitemap / 子 sitemap | **没有**。sitemap.xml 是 urlset(非 index),102 条全部为频道页/登录页,lastmod 全部冻结在 2018-12-20,无任何子 sitemap 或内容页 URL | 一手实测 |
| 1b | RSS/订阅源 | **未找到**。robots.txt 仅声明 `Sitemap: https://vip.kingdee.com/sitemap.xml`,无任何 feed 声明;搜索无结果 | 一手实测 + 二手(搜索) |
| 1c | 开放平台 API / 数据导出 | **有 API,但都是业务 API,不含社区文档**。openapi.open.kingdee.com=金蝶云星空开放平台(单据/基础资料 WebAPI,需第三方授权 AppID/Secret);open.kingdee.com 302→`/K3Cloud/Open/home`;另见 API 市场 cloud.kingdee.com/kae、API 网关 mcapi.kingdee.com。**均不可用于社区语料枚举**;无「社区数据导出」公开渠道 | 一手实测(域名探测)+ 二手(搜索确认范围) |
| 2 | 移动端 m.vip.kingdee.com | **无法建立连接**(DNS 可解析,TCP 连接失败 code=000);WebSearch 无任何 m 站接口痕迹。无「分页上限不同」的证据 | 一手实测(本机网络,见诚实边界)+ 二手(搜索) |
| 3a | **help.open.kingdee.com 产品手册(DokuWiki)——本次唯一新发现的更便宜通道** | 「金蝶云产品手册」DokuWiki,`robots: index,follow`;`?do=index` **一次请求返回 1077 个页面 URL**(结构化全站索引);每页自带 `do=export_raw`/`export_xhtml` 原文导出链接;`sitemap.xml.gz` 返回 302(未启用)。覆盖=功能级操作手册(促销管理/信用管理/人人报销等 168 个起始入口) | 一手实测 |
| 3b | 第三方镜像/聚合站 | **未找到**。无 vip.kingdee.com 镜像/聚合/数据集;GitHub 无针对金蝶云社区的公开爬虫项目 | 二手(搜索) |
| 4 | robots 事实的含义 + 更优枚举策略 | `User-agent: * Disallow: /` + 主流搜索引擎 `Allow: /` = **站方明确拒绝通用代理批量抓取,仅欢迎搜索引擎**;伪装成 Googlebot 抓取=欺骗性规避,不可取。中国判例把 Robots 协议当「行业商业道德」看(百度诉360),但大众点评诉百度确立**真正红线是「实质性替代」而非抓取本身**。更优枚举见 §2(组合拳:手册走 DokuWiki 索引、社区增量走 sortsType=2 时间网格 + 图游走,而不是加宽关键词空间) | 一手实测 + 二手(法律判例) |

## 1. 逐条证据

### 1.1 sitemap.xml:102 条频道页,2018 年后未更新(一手实测)

直接 GET `https://vip.kingdee.com/sitemap.xml`(17411 字节):

- 根元素 `<urlset>`,**非** `<sitemapindex>` → 不存在子 sitemap 链;
- `<loc>` 计数 = **102**,内容全部为频道/功能页(`/find`、`/guide`、`/answer`、`/circle`、`/school`、`/news/385`…)甚至混入 `/login?next=/people/...` 个人页;
- 所有 `<lastmod>` 均为 `2018-12-20` → **7 年多未维护的静态残留,不承载任何内容页 URL**;
- robots.txt(200,2007 行内)唯一声明的入口就是这一个 sitemap,没有其他 feed。

**含义:sitemap 通道对语料枚举价值 = 0。**

### 1.2 开放平台域名全景(一手实测 + 二手)

| 域名 | 实测结果 | 对语料采集的意义 |
|---|---|---|
| `openapi.open.kingdee.com/` | 200,SPA,`<title>金蝶云星空开放平台</title>` | 业务 WebAPI 文档门户([ApiDoc](https://openapi.open.kingdee.com/ApiDoc)、[ApiHome](https://openapi.open.kingdee.com/ApiHome));搜索确认其范围 =「单据和基础信息操作全面开放,提供 .Net/Java/Python SDK」,**需第三方授权 AppID/Secret,不含社区知识内容** |
| `open.kingdee.com/` | 200,980 字节 JS 跳转页 → `document.location.href = "/K3Cloud/Open/home"` | 同上(K3Cloud 开放平台门户) |
| `cloud.kingdee.com/kae/`、`mcapi.kingdee.com` | 未直接探测;搜索显示为 API 市场/API 网关 | 企业服务能力开放,与社区文档无关 |

搜索证据(未找到社区数据导出渠道):搜索词 `「m.vip.kingdee.com」OR「金蝶云社区」数据导出 OR 开放知识库 API 官方` → 仅命中社区官网、[API 市场](https://cloud.kingdee.com/kae/)、[苍穹应用市场文档](https://appmarket.kingdee.com/index/document/index/702892094201700352) 等业务开放入口;**无任何「社区知识库开放/导出」官方渠道**。

### 1.3 移动端 m.vip.kingdee.com(一手实测,含诚实边界)

- `nslookup` 可解析(本机走 fake-IP DNS);`curl https://m.vip.kingdee.com/` 两次均 **code=000,TCP 无法建立**,size=0;
- WebSearch `m.vip.kingdee.com` 仅在社区官网描述里出现「移动端域名即 m.vip.kingdee.com」,无独立接口泄露/文档。

**诚实边界**:本机网络走代理(fake-IP),code=000 不能 100% 排除「代理规则拦了该域名」的可能;但结合搜索零痕迹,倾向判定**无独立移动端站点/接口**,更不存在「分页上限不同」的证据。不值得再花请求验证。

### 1.4 help.open.kingdee.com:被忽视的结构化手册通道(一手实测,本次核心发现)

直接 GET `https://help.open.kingdee.com/` → 302 → `https://help.open.kingdee.com/dokuwiki/doku.php`:

- `<title>start - 金蝶云产品手册</title>`,`<meta name="robots" content="index,follow">` → **站方主动欢迎索引**;
- 起始页含 **168 个**去重内部链接,全部为功能级手册页(业务流程、促销管理、信用管理、人人报销、供应商协同…);
- `doku.php?do=index` **一次请求(265KB)返回 1077 个去重页面 URL** —— 等价于全站页面清单,枚举成本从「每 25 条一页」降为「每 1077 个标题一请求」;
- DokuWiki 原生导出:每页自带 `?do=export_raw&id=<页名>`(纯 wiki 源码)与 `?do=export_xhtml`(干净 XHTML)链接,单页全文可匿名直取;
- `sitemap.xml.gz` 返回 302(站点未启用 sitemap),但 `do=index` 已等价替代。

**适用范围与边界**:该 wiki 是**产品操作手册**(对应 kd 语料分类里的「手册型知识/帮助中心产品文档」,恰好是现有语料与官方金标里的短板类),**不能替代**社区问答(answer)/文章(article)/知识(knowledge)三类实体——后三类仍只能走 /api/search 或图游走。

### 1.5 镜像/聚合/第三方爬虫(二手,均未找到)

- 搜索词:`"vip.kingdee.com" 镜像 OR 聚合 OR 抓取 爬虫 语料` → 仅命中官网与无关新闻爬虫教程,「没有发现任何关于该站被镜像、爬取或构建语料的公开信息」;
- 搜索词:`github vip.kingdee.com 爬虫 OR spider OR crawler 金蝶云社区` → GitHub 上只有 [kingdee topics](https://github.com/topics/kingdee)(ERP WebAPI SDK/MCP/Skill 类),**无社区内容爬虫项目**。

## 2. robots.txt 事实的合规含义与更优枚举策略

### 2.1 对「批量采集标题+摘要」的含义

实测 robots.txt:`User-agent: * → Disallow: /`;`Googlebot / Baiduspider / bingbot / 360Spider / Sogou / Bytespider / YisouSpider → Allow: /`。三层含义:

1. **站方意志明确**:通用代理(即本项目这类)被整体拒绝,允许的只有搜索引擎 → 「伪装成 Googlebot/Baiduspider 绕过」属于欺骗性规避 Robots 协议,不可取;
2. **中国判例把 Robots 协议当作行业商业道德的体现**(百度诉奇虎 360 案,北京一中院;参见[上海知产法院张春波文章](https://www.shzcfy.gov.cn/detail.jhtml?id=10010027)、[集佳律所爬虫实务解读](https://www.jmplaw.cn/?post_type=products&page_id=20533));
3. **但判例真正的红线是「实质性替代」,不是抓取行为本身**:大众点评诉百度案(上海知产法院)认定百度抓取本身不违反 Robots 协议,违法点在于**大量全文展示构成对原服务的实质性替代**(见上文同链接)。

→ 对本项目的落点:本地语料用于**个人检索增强、不转载不展示不替代原站**,不在「实质性替代」打击面内;但与站方 Robots 意志冲突是事实。防御姿态 = 保持 kd 现有铁律(人类频率、低总量、带 UA 诚实标识、语料不对外分发),并把**请求总量**当作合规预算来省——这正是找更便宜通道的动机。

### 2.2 比「关键词空间切片×深分页」更优的枚举策略(按 ROI 排序)

| 策略 | 通道 | 成本对比 | 等级 |
|---|---|---|---|
| **① 手册类走 DokuWiki 索引** | `help.open.kingdee.com/dokuwiki/doku.php?do=index` 拿全清单 → 逐页 `export_raw` | 1077 个标题 ≈ **1 次请求**;对比关键词切片覆盖同类手册要数千请求 | 一手实测 |
| **② 时间网格增量(sortsType=2)** | /api/search `sortsType=2`(时间倒序,本项目已实测)+ 窄域词 × **浅分页**:窄查询的结果集远小于 1250 硬顶,实际页数≈结果集/25,且增量只需扫到上次水位 | 42 词×3 页的 v5 发现流程已验证可行;成本与词表宽度线性,而非与全站总量线性 | 一手(本项目 API.md/discovery-report-v5) |
| **③ 推荐图谱游走** | 文章详情页 `recommendArray`(每篇约 10 篇相关+摘要),从 usage 种子 BFS | 每 1 次详情请求白拿约 10 篇邻居标题+摘要,天然命中「相关问题簇」,无 1250 硬顶 | 一手(交接文档 6 / 调研报告 §6) |
| ④ 分类 ID / 实体类型切片 | `type=knowledge\|answer\|article` 与 `productId` 过滤已证实可用;**分类 ID(categoryId)切片参数未证实存在**——分类目录页 Nuxt 数据待探测(调研报告 §6 遗留项) | 若存在则可与 ② 叠加压缩词表 | 一手(type/productId)+ 未证实(categoryId) |

**汇总**:「一个查询硬顶 50 页」的墙只对宽词存在;策略 ①②③ 组合下,枚举成本的主导项从「关键词×深度」变为「词表宽度(已有 42 词)+ 图邻域 + 一次 DokuWiki 全量索引」,估算请求量从最多 ~7500 降至数百量级,且 ①③ 完全不经过被 Disallow 的 /api/search 主通道。

## 3. 诚实边界

- m.vip.kingdee.com 的 code=000 受本机代理环境干扰可能,未从外部网络复核;
- `openapi.open.kingdee.com/ApiDoc`、`cloud.kingdee.com/kae` 为 SPA/搜索转述,未逐页确认「绝无社区内容接口」(但其产品定位与授权模式与之相悖,风险极低);
- help.open.kingdee.com 手册的**更新频率与官方金标覆盖率未验证**(1077 页是否含发版说明/苍穹/旗舰版各产品线,需落盘后对金标抽测);
- categoryId 切片参数存在性:未探测(留给分类页 Nuxt 数据解析那一项);
- 法律判断为二手综述+本项目解读,非法律意见。

## 4. 参考来源

一手实测(2026-09-06,共 10 次请求):
- `https://vip.kingdee.com/robots.txt`(200;`User-agent: * Disallow: /`,搜索引擎 Allow,声明 sitemap.xml)
- `https://vip.kingdee.com/sitemap.xml`(200;urlset、102 loc、lastmod 全 2018-12-20)
- `https://openapi.open.kingdee.com/`(200;SPA「金蝶云星空开放平台」)
- `https://open.kingdee.com/`(200;JS 跳转 `/K3Cloud/Open/home`)
- `https://m.vip.kingdee.com/`(TCP 连接失败 ×2)
- `https://help.open.kingdee.com/` → `/dokuwiki/doku.php`(200;「金蝶云产品手册」,robots index,follow)
- `help.open.kingdee.com/dokuwiki/doku.php?do=index`(200;1077 个去重页面 URL)/ `sitemap.xml.gz`(302)

二手:
- [金蝶云星空开放平台 ApiDoc](https://openapi.open.kingdee.com/ApiDoc) / [ApiHome](https://openapi.open.kingdee.com/ApiHome)(WebAPI 定位与 SDK,搜索确认)
- [金蝶云平台 API 市场](https://cloud.kingdee.com/kae/)、[苍穹应用市场开放文档](https://appmarket.kingdee.com/index/document/index/702892094201700352)
- [kingdee · GitHub Topics](https://github.com/topics/kingdee)(无社区爬虫项目)
- [上海知产法院:大众点评诉百度与 Robots 协议](https://www.shzcfy.gov.cn/detail.jhtml?id=10010027)、[集佳:爬虫协议在爬取行为正当性认定中的作用](https://www.jmplaw.cn/?post_type=products&page_id=20533)

本项目一手记录:
- `docs/API.md`(/api/search 参数:productId/type/sortsType/pageSize≤50)
- `docs/discovery-report-v5.md`、`docs/research/2026-09-06-official-vector-recall.md` §6(sortsType=2 时间倒序、recommendArray 图游走、金标 0/10 不可搜)
