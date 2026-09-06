# 调研:关键词枚举 deep web 语料 —— 工程实践侧(2026-09-06)

> 与同日《2026-09-06-coverage-estimation-and-enumeration.md》(学术侧)互补:本篇只找工程实践侧——工程博客、开源项目、产品文档里**真的被实现过**的覆盖度估计、饱和停止、滚动扩词、时间窗切片。
> 方法:一手来源优先(官方文档/GitHub/官方博客);不请求 vip.kingdee.com / help.open.kingdee.com(上游抓取在跑)。
> 「路线」指本项目四条升级路:A=覆盖度仪表,B=饱和即停,C=snowball 滚动扩词,D=时间窗分区。

## 0. 总结论(先看这个)

| # | 发现 | 路线 | 可抄程度 | 链接 | 等级 |
|---|---|---|---|---|---|
| 1 | **Crawl4AI Adaptive Crawling 是唯一把"饱和即停"做成带默认阈值的成品开源实现**:信息增益停止判据 4 条 + 饱和度公式 + 具体默认值(见 §1) | B、A | 判据与阈值可直接照抄,代码是"内容相似度"版,需改造成"文档 ID 集合"版 | [docs](https://docs.crawl4ai.com/core/adaptive-crawling/) / [API](https://docs.crawl4ai.com/api/adaptive-crawler/) / [设计文档](https://github.com/unclecode/crawl4ai/blob/main/PROGRESSIVE_CRAWLING.md) / [digest()](https://docs.crawl4ai.com/api/digest/) | 一手(官方文档+源码级设计文档) |
| 2 | **时间窗切片绕分页钳制是工业界标准做法**,有逐字对应的公开实现:Apify Reddit scraper 按周切窗口绕 1000 条钳制,Aircall 官方文档用日期窗绕 10000 条钳制 | D | 思路照抄即可;Apify 是闭源 actor,但行为文档写得很细 | [Apify Reddit Scraper](https://apify.com/betterdevsscrape/reddit-scraper) / [Aircall Docs](https://developer.aircall.io/docs/work-with-call-data) / [Kustomer FAQ](https://help.kustomer.com/pt_br/kustomer-api-frequently-asked-questions-rJPDs3yAZg) | 一手(产品文档) |
| 3 | **Common Crawl 官方博客把"站点覆盖度"当正式工程问题写**,并引用其工程师 2026 新论文:两次爬取的 containment(=双列表 capture-recapture)+ discovery curve 估覆盖率 c 与存活率 α;承认"从未见过的 URL 无法用 sitemap 算,只能用数学" | A | containment 一阶估计(两份词表交集/并集)是我们零成本可算的;论文是学术侧补充 | [CC 博客](https://commoncrawl.org/blog/measuring-crawled-coverage-of-a-website-in-common-crawl) / [arXiv 2607.13636](https://arxiv.org/abs/2607.13636) | 一手(官方博客+论文) |
| 4 | **用搜索接口(非链接爬取)枚举站点**有实战文章群:Scrapecrow 的 Search API discovery 教学、Secjuice 的 deep-web 数据库刮取、Datablist 的多查询批量刮 Google 绕单查钳制 | (全部) | 坑位清单(§2)直接对照自查 | [Scrapecrow](https://scrapecrow.com/web-scraping-discovery-search.html) / [Secjuice](https://secjuice.com/osint-scraping-deep-web-databases-with-python/) / [Datablist](https://www.datablist.com/how-to/scrape-google-multi-queries) / [SO 经典问题](https://stackoverflow.com/questions/48521675/how-to-scrape-all-possible-results-from-a-search-bar-of-a-website) | 一手/二手(实战文) |
| 5 | **LLM 滚动扩词**(2023+):开源最接近的是 Jina AI llm-query-expansion(为检索扩词)与 Haystack Query Expansion 教程;**"从已抓内容提词→再爬"的成体系开源 snowball 未找到**,仍是 Ntoulas 式自研 | C | 思想可借,无现成枚举器可抄 | [Jina](https://github.com/jina-ai/llm-query-expansion) / [Haystack](https://haystack.deepset.ai/blog/query-expansion) | 一手(开源) |
| 6 | **RAG 垂直语料库的覆盖度/新鲜度**:kapa.ai 长文(change detection+增量更新+监控)与 Towards AI 增量索引长文是 2024-26 最完整的工程写法;均默认"全量基线+增量刷新",**无人做覆盖度仪表** | A(仪表的"新鲜度半边") | 增量刷新与内容哈希去重模式可抄;覆盖度部分仍要靠学术侧 Chao/iNEXT | [kapa.ai](https://www.kapa.ai/library/how-to-keep-a-rag-knowledge-base-in-sync-with-changing-docs) / [Towards AI](https://pub.towardsai.net/building-a-production-ready-rag-system-with-incremental-indexing-ee42cfbfef7f) | 一手/二手(实战长文) |
| 7 | **中文社区:没有找到**"搜索接口全量枚举+覆盖度/停止条件"的专题实战文章;相关讨论以"队列耗尽/新增 URL 递减/去重率上升"等口语化形式散落(vivo 技术博客等);知乎/掘金对"关键词泛采集"的共识坑=游标分页+单关键词上限+拆长尾词/时间分段补覆盖 | — | 佐证本项目的做法在中文圈也属前沿,无现成轮子 | [vivo 博客](https://www.cnblogs.com/vivotech/p/16695804.html) / [知乎讨论](https://www.zhihu.com/question/29778227) | 一手/二手 |

---

## 1. 覆盖率/规模估计的工程实现(路线 A、B)

### 1.1 Crawl4AI Adaptive Crawling —— 唯一带默认阈值的成品(最高价值)

信息检索领域"信息觅食理论"(information foraging / Patch foraging)的工程化。它问的问题与我们逐字相同:**"再抓一页,还会带来新信息吗?"**

**停止判据(设计文档 §4.2,四条满足其一即停):**

1. 信息饱和度 `IS ≥ θ`(θ 是目标置信度阈值);
2. 增益枯竭:`d(IS)/d(crawls) < ε`——信息饱和度对抓取页数的导数低于 ε,即"边际增益趋零";
3. 预算耗尽:`crawls ≥ max_pages`;
4. 无有前景的下一链接:`max(ExpectedGain) < min_gain`。

**饱和度定义(可直接借用的公式):**

```
Saturation = 1 − ΔInfo(K_n) / ΔInfo(K_1)
```

即"最后一段抓取带来的信息增量 ÷ 第一段的信息增量",趋近 1 即饱和。对我们的版本:把 `Info` 换成**新文档 ID 计数**——`Saturation(word) = 1 − 新文档数(第 p−N..p 页) / 新文档数(第 1..N 页)`。

**官方默认阈值(AdaptiveCrawler,embedding 策略):**

| 参数 | 默认值 | 含义 |
|---|---|---|
| `confidence_threshold` | 0.7 | 达到 70% 覆盖即认为"够了" |
| `min_pages_per_batch` | 3 | 每批最少抓 3 页再评估(避免单页噪声) |
| `max_pages` | 20 | 硬预算 |
| `min_gain_threshold` | 0.1 | 单页信息增益低于 0.1 即视为枯竭 |
| `min_relative_improvement` | 0.1 | 新批次相对上批覆盖度提升 <10% 即停(embedding 版) |
| `overlap_threshold` | 0.85 | 页面间相似度 >0.85 视为冗余 |
| `top_k_links` | 3 | 每页只跟进增益最高的 3 个链接 |

**对我们最关键的差别(诚实边界)**:Crawl4AI 的"信息"是**页面内容相对一个查询的信息量**(embedding/词覆盖),面向"收集够回答一个问题的材料";我们是**新文档 ID 相对整个语料库的边际计数**,面向"枚举宇宙"。所以不能直接调它的 API,但判据结构(批量评估+相对改进率+饱和公式)和默认阈值数量级可以直接搬。它与学术侧 He WSDM 2013 的 tail-focused 思想在工程上合流:**"每词最后 N 页的新文档率"就是 per-word 停止信号**。

- 官方文档(含 `get_saturation()` / `get_coverage_stats()` API):<https://docs.crawl4ai.com/core/adaptive-crawling/> 、<https://docs.crawl4ai.com/api/adaptive-crawler/>
- 设计文档(停止判据推导):<https://github.com/unclecode/crawl4ai/blob/main/PROGRESSIVE_CRAWLING.md>
- 第三方解读(ScrapFly):<https://scrapfly.io/blog/posts/crawl4AI-explained>("coverage=查询词覆盖、consistency=页面一致性、saturation=边际收益枯竭")

### 1.2 Common Crawl:站点级覆盖度是正式工程课题

CC 官方博客承认"某站被抓了多少页"比想象难答(爬虫按变化频率降频重访,导致每爬净增量变小、覆盖率反而难读),给出两条工程路线:

1. **CrawlDB 状态机计数**:把 URL 分状态(fetched/not-modified 等),"fetched 态"页面数即"新鲜已抓页数"估计——对应我们的**文档状态表**(已落盘/已去重/软 404);
2. **双列表 containment + discovery curve**:其工程师论文 *Measuring What the Crawler Sees*(arXiv 2607.13636,2026)用两次爬取的包含关系在 urn 模型下同时解出覆盖率 c 与存活率 α,并诚实指出"壳层不均匀时单一参数不够,要持久核+壳两层"——这正是学术侧 Chao92 "捕获不均"问题的 CC 实证版。

映射:我们任何两轮词表(或一轮词表 vs 一轮时间窗)的交集/并集就是 CC 式 containment 的零成本近似;CC 论文警告的"不均匀性让单参数失真"与学术侧"f1 偏高导致 N̂ 偏高"结论一致。

### 1.3 其他工程侧估计用法(非爬虫域,方法同构)

- **IBM Jazz 缺陷估计**:两个独立评审者发现缺陷的重叠做 capture-recapture 估总缺陷数([ResearchGate](https://www.researchgate.net/publication/266630368_Defect_Estimation_using_Capture-Recapture_in_IBM_Jazz))——结构上=两份独立词表重叠估总量;
- **Troy Magennis 的 latent defect estimation 交互笔记本**(Observable):mark-and-recapture 估"还没发现的缺陷数",可交互调参,公式直接可看([链接](https://old.observablehq.com/@troymagennis/latent-defect-estimation))。

**诚实边界**:工程界(Scrapy/Zyte/Crawlee/Firecrawl)**没有**找到把 Chao/Good-Turing 用于决定爬虫何时停的实现;Zyte、Crawlee 博客检索"diminishing returns / when to stop / coverage"均无内容命中(Crawlee 停止手段只有 `maxRequestsPerCrawl`;Firecrawl 只有 `limit` 默认 10000 页 + "按实际规模设 limit"的常识建议,[API 文档](https://docs.firecrawl.dev/api-reference/endpoint/crawl-post)、[官方博客](https://www.firecrawl.dev/blog/mastering-the-crawl-endpoint-in-firecrawl))。"覆盖度决定停止"在开源工程里目前只有 Crawl4AI 一家;Chao 系估计仍要按学术侧文档自算(零请求成本,iNEXT 一行)。

## 2. Query-based enumeration 实战坑位清单(路线 D、B 佐证)

| 来源 | 实战内容 | 对应我们的坑 |
|---|---|---|
| [Scrapecrow: Web Scraping Target Discovery — Search API](https://scrapecrow.com/web-scraping-discovery-search.html) | 逆向站点搜索框拿底层 search API,"往往是发现目标最好的方式";配套 [总览篇](https://scrapecrow.com/web-scraping-discovery.html)(sitemap/index 篇) | 路线背书:搜索接口枚举是被承认的一等公民方法 |
| [Secjuice: OSINT Scraping Deep Web Databases with Python](https://secjuice.com/osint-scraping-deep-web-databases-with-python/) | 批量对多个 deep-web 搜索端点发查询的写法 | 多查询并行时的礼貌限速模板 |
| [Datablist: Scrape Google from multiple queries at once](https://www.datablist.com/how-to/scrape-google-multi-queries) | 用查询生成+批量刮绕单查询结果上限(Google SERP ~300 条钳制的标准破解=多组合词) | 与我们"宽词 2500 钳制"同构;区别是我们有真时间窗参数,比组合词切更便宜 |
| [StackOverflow: How to scrape all results from a search bar](https://stackoverflow.com/questions/48521675/how-to-scrape-all-possible-results-from-a-search-bar-of-a-website) | 经典问答:穷举搜索端点结果空间的通用讨论 | 坑位对照 |
| [Webscraper.io: Scaling to millions of pages](https://webscraper.io/blog/scaling-web-scraping-from-thousands-to-millions-of-pages) | 去重经济学:"5% 重复率在百万目标上=5 万次白抓";断点续爬与 seen-set 外置 | 去重日志落盘的理由(我们的 f1/f2 就要从这份数据重建) |
| [Medium/Towardsdev: Web Scraping at Scale](https://medium.com/towardsdev/web-scraping-at-scale-rate-limiting-deduplication-and-async-pipelines-that-actually-work-in-prod-38862e7ef727) / [dev.to: 1K→10M pages](https://dev.to/agenthustler/web-scraping-at-scale-from-1k-to-10m-pages-4ggk) | 429 当退避信号、URL 去重外置存储、"别把 seen 集放内存"、不丢进度的重试 | 1 请求/秒红线下的工程卫生 |

**时间窗切片的逐字对应实现(路线 D 最有力证据):**

- **Apify Reddit Scraper**(官方 actor 市场):明确写着把搜索请求**按周切日期窗**绕"单查询 ~1000 条"分页钳制,实现整个 subreddit 存档级采集([apify.com/betterdevsscrape/reddit-scraper](https://apify.com/betterdevsscrape/reddit-scraper))——与"sortsType=2 时间倒序 + 按年/季切窗绕 2500 钳制"完全同构,窗口粒度自适应(结果密→周,结果疏→月/季)是它文档给出的实践;
- **Aircall 官方文档**:用 `from`/`to` 把大导出拆成小日期窗绕 10000 条上限([developer.aircall.io](https://developer.aircall.io/docs/work-with-call-data));
- **Kustomer FAQ**:Search API 100 页硬顶 + 按日期范围查([help.kustomer.com](https://help.kustomer.com/pt_br/kustomer-api-frequently-asked-questions-rJPDs3yAZg))。

## 3. 滚动扩词 / LLM 查询自动生成(路线 C)

| 项目/文章 | 是什么 | 对 snowball 的可用性 | 链接 | 等级 |
|---|---|---|---|---|
| Jina AI `llm-query-expansion`(开源) | 用 LLM 为 embedding 检索做查询扩展/改写,含评测 | 候选词池的生成器,不是枚举器 | <https://github.com/jina-ai/llm-query-expansion> | 一手(开源) |
| Haystack "Advanced RAG: Query Expansion" | 关键词搜索场景的扩词教程(multi-query) | 同上,教程级 | <https://haystack.deepset.ai/blog/query-expansion> | 一手(官方教程) |
| ACM HT 2025 "Coverage-Aware Web Crawling for Domain-Specific Crawling" | **最对症**:覆盖缺口→生成查询→经领域目录展开为种子 URL;ACM 全文公开 | 是学术侧 MDPI 2025 之外的第二个"LLM/自动生成查询补覆盖"实证;仍非"关键词接口全库枚举"闭环 | <https://dl.acm.org/doi/full/10.1145/3813822.3814125> | 一手(论文,工程向) |
| Springer Computing(2026) "LLMs applied to web scraping and web crawling: a systematic review" | LLM×爬虫系统综述,可当 2023+ 工程实践地图查漏 | 用于确认"没漏掉大项目" | <https://link.springer.com/article/10.1007/s00607-026-01666-5> | 一手(综述) |
| **"从已抓内容提词再爬"的开源 snowball 实现** | — | **未找到**。工程界最接近的仍是 Crawl4AI 的自适应(内容级,非词表层)与 OSINT 圈的零散脚本;词表滚动闭环维持自研结论(Ntoulas 2005 内核 + 学术侧文档 §3) | — | 诚实边界 |

## 4. RAG 语料构建视角:覆盖度/新鲜度/增量(2024-2026)

| 文章 | 核心策略 | 对我们可用部分 | 链接 | 等级 |
|---|---|---|---|---|
| **kapa.ai "How to Keep a RAG Knowledge Base in Sync with Changing Docs"** | change detection(内容哈希/最后修改)→增量更新→监控,面向文档站爬取 | 增量刷新模式:重抓时先 HEAD/哈希比对,只入新变文档——对应我们二次巡抓时省预算;**全量基线+周期增量**是行业默认 | <https://www.kapa.ai/library/how-to-keep-a-rag-knowledge-base-in-sync-with-changing-docs> | 一手(厂商工程长文) |
| **Towards AI "Production-Ready RAG with Incremental Indexing"** | 文档级 upsert/删除传播/增量重嵌入,内容哈希去重 | 语料库元数据设计(版本、hash、seen 时间)可照抄 | <https://pub.towardsai.net/building-a-production-ready-rag-system-with-incremental-indexing-ee42cfbfef7f> | 二手(实战长文) |
| Continue Docs "Custom Code RAG" | 明确立场:"增量刷新通常就够且便宜,除非源被整个重写" | 支撑"增量巡抓优先于全量重爬"的引用 | <https://docs.continue.dev/guides/custom-code-rag> | 一手(官方文档) |
| Google Cloud Vertex RAG Engine "Manage your RAG corpus" | 官方语料管理原语(import/import files/增量) | 大厂默认同样是"无覆盖度仪表,只有预算与增量" | <https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/manage-your-rag-corpus> | 一手(官方文档) |

诚实边界:**没有**找到任何 2024-26 实战长文同时具备"覆盖度估计+新鲜度+增量更新"三件套;"覆盖度"在 RAG 工程语境里默认等于"选好源+抓全站链接",不等于我们这种搜索接口枚举的统计覆盖。我们的覆盖度仪表要靠学术侧 Chao/iNEXT 补,RAG 文章只补"新鲜度/增量"半边。

## 5. 中文社区检索结果

- **专题文章:未找到**。用"停止条件/覆盖度/采样饱和 + 语料采集 + 掘金/博客园/知乎"多轮检索,没有命中"搜索接口全量枚举 + 覆盖度量化的停止判据"类文章;
- [vivo 互联网技术:爬虫与反爬虫技术简介(博客园)](https://www.cnblogs.com/vivotech/p/16695804.html):停止条件仅表述为"队列耗尽"——大厂科普层面对此也无更多着墨;
- 中文圈"关键词泛采集"的共识坑(知乎 [1](https://www.zhihu.com/question/29778227)、[2](https://www.zhihu.com/question/268204922)、掘金 [游标分页实战](https://juejin.cn/post/6844904014191165448)):搜索接口多为游标分页、单关键词有总条数上限(约千级)、主流破解=**拆长尾词 + 按时间分段 + 栏目页/sitemap 补漏**——与我们四条路线方向一致但无量化;
- [龙石数据:AI 大模型的数据基础](https://www.longshidata.com/blog/c/c2025012401.html):中文语料视角提"智能化爬虫动态调策略",无实现细节。

## 6. 落地映射(工程来源 → 四条路线)

1. **覆盖度仪表(A)**:学术侧 Chao1/Ĉ=1−f1/n 为主力;工程侧借 CC 的 containment(任两轮词表交集/并集,零成本)做交叉验证;元数据表按 Towards AI 的 hash/seen 字段设计,兼做增量;
2. **饱和即停(B)**:直接抄 Crawl4AI 判据结构——`每批 ≥ min_pages_per_batch 页` 才评估;`新文档率(尾段)/新文档率(首段) < 0.1` 即停该词(=它的 min_relative_improvement/min_gain);置信目标先用它的 0.7 量级起步,再用 iNEXT 校准成我们自己的数;
3. **snowball(C)**:无现成轮子,自研闭环照 Ntoulas(学术侧);LLM 候选词生成可挂 Jina/Haystack 的扩词 prompt 结构;
4. **时间窗分区(D)**:Apify Reddit 的"窗口粒度自适应(密→周、疏→季)"是唯一有公开行为文档的同构实现,直接按它设计:先按年探测,命中数超 2500 的年再二分到季/月;Aircall/Kustomer 佐证这是 API 厂商认可的正规绕法(而非对抗手段)。

## 7. 参考来源汇总

- Crawl4AI:[Adaptive Crawling](https://docs.crawl4ai.com/core/adaptive-crawling/) / [AdaptiveCrawler API](https://docs.crawl4ai.com/api/adaptive-crawler/) / [PROGRESSIVE_CRAWLING.md](https://github.com/unclecode/crawl4ai/blob/main/PROGRESSIVE_CRAWLING.md) / [digest()](https://docs.crawl4ai.com/api/digest/) / [ScrapFly 解读](https://scrapfly.io/blog/posts/crawl4AI-explained)
- Common Crawl:[Measuring Crawled Coverage of a Website](https://commoncrawl.org/blog/measuring-crawled-coverage-of-a-website-in-common-crawl) / [Paris et al., arXiv 2607.13636](https://arxiv.org/abs/2607.13636) / [CC crawl statistics](https://commoncrawl.github.io/cc-crawl-statistics/plots/crawlsize)
- 时间窗切片:[Apify Reddit Scraper](https://apify.com/betterdevsscrape/reddit-scraper) / [Aircall](https://developer.aircall.io/docs/work-with-call-data) / [Kustomer FAQ](https://help.kustomer.com/pt_br/kustomer-api-frequently-asked-questions-rJPDs3yAZg)
- 搜索接口枚举实战:[Scrapecrow](https://scrapecrow.com/web-scraping-discovery-search.html) / [Secjuice](https://secjuice.com/osint-scraping-deep-web-databases-with-python/) / [Datablist](https://www.datablist.com/how-to/scrape-google-multi-queries) / [SO 48521675](https://stackoverflow.com/questions/48521675/how-to-scrape-all-possible-results-from-a-search-bar-of-a-website) / [Webscraper.io 规模化](https://webscraper.io/blog/scaling-web-scraping-from-thousands-to-millions-of-pages) / [Towardsdev](https://medium.com/towardsdev/web-scraping-at-scale-rate-limiting-deduplication-and-async-pipelines-that-actually-work-in-prod-38862e7ef727) / [dev.to](https://dev.to/agenthustler/web-scraping-at-scale-from-1k-to-10m-pages-4ggk)
- Firecrawl(对照组,无覆盖度停止):[/crawl API](https://docs.firecrawl.dev/api-reference/endpoint/crawl-post) / [官方博客](https://www.firecrawl.dev/blog/mastering-the-crawl-endpoint-in-firecrawl)
- LLM 扩词:[Jina llm-query-expansion](https://github.com/jina-ai/llm-query-expansion) / [Haystack Query Expansion](https://haystack.deepset.ai/blog/query-expansion) / [Coverage-Aware Crawling, ACM HT 2025](https://dl.acm.org/doi/full/10.1145/3813822.3814125) / [Springer 系统综述 2026](https://link.springer.com/article/10.1007/s00607-026-01666-5)
- capture-recapture 工程用法:[IBM Jazz 缺陷估计](https://www.researchgate.net/publication/266630368_Defect_Estimation_using_Capture-Recapture_in_IBM_Jazz) / [Troy Magennis Observable](https://old.observablehq.com/@troymagennis/latent-defect-estimation)
- RAG 语料:[kapa.ai 同步长文](https://www.kapa.ai/library/how-to-keep-a-rag-knowledge-base-in-sync-with-changing-docs) / [Towards AI 增量索引](https://pub.towardsai.net/building-a-production-ready-rag-system-with-incremental-indexing-ee42cfbfef7f) / [Continue Docs](https://docs.continue.dev/guides/custom-code-rag) / [Vertex RAG Engine](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/manage-your-rag-corpus)
- 中文:[vivo 爬虫简介](https://www.cnblogs.com/vivotech/p/16695804.html) / [知乎关键词泛采集](https://www.zhihu.com/question/29778227) / [掘金游标分页](https://juejin.cn/post/6844904014191165448) / [龙石数据](https://www.longshidata.com/blog/c/c2025012401.html)
