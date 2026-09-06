# 调研:关键词枚举 deep web 语料 —— 覆盖度估计与查询自动生成(2026-09-06)

> 起因:kd 语料抓取用 163 个关键词 × 逐页枚举,词间重叠严重但无法知道"还剩多少没抓到"。
> 方法:一手来源 = arXiv / ACM DL / 期刊原文 + 官方工程文档;不请求 vip.kingdee.com / help.open.kingdee.com(上游抓取在跑)。
> 结论表带证据链接与等级;每节末给「映射到本项目」。

## 1. 这个问题在学术界的名字和经典文献

学术名字:**Hidden/Deep Web Crawling / Siphoning / Query Selection(查询选择)**——通过查询接口枚举一个只暴露搜索框的数据库。我们的「163 词 × 逐页抓」在文献里就叫 *textual deep-web siphoning*。

| 文献 | 核心思想一句话 | 与我们查询选择的关系 | 链接 | 等级 |
|---|---|---|---|---|
| **Ntoulas, Zerfos, Cho, "Downloading Textual Hidden Web Content Through Keyword Queries"(JCDL 2005,siphoning 开山)** | 先发少量探针查询采样数据库,用返回文档估计每个候选词的"期望收益"(新文档数),贪心选词,迭代滚雪球 | **正是我们"标题高频名词"做法的学术版**;它证明了"从已抓文档提词→估收益→贪心"比固定词表覆盖高一个档次 | [ACM DL](https://dl.acm.org/doi/10.1145/1065385.1065407) / [Semantic Scholar](https://www.semanticscholar.org/paper/d202ef68c280f3dd0839e67471fc0b78a3fa920b) | 一手(论文) |
| **Madhavan et al., "Google's Deep Web Crawl"(VLDB 2008)** | Google 工业级方案:理解 HTML 表单→自动生成查询→把"surface 过"的页面并入主索引;按查询模板族组织枚举,收益衰减即降级该族 | 证明关键词枚举在几十亿页面规模上可行;其"模板族+收益衰减"是我们 per-词翻页停止判据的更粗粒度版本 | [ACM DL](https://dl.acm.org/doi/10.14778/1454159.1454163) / [Google PDF](https://research.google.com/pubs/archive/34618.pdf) | 一手(工业论文) |
| **He, Patel, Zhang, Chang, "Accessing the Deep Web: A Survey"(CACM 2007)** | 综述:deep web 访问三段论——接口发现/模式抽取/查询选择与内容获取 | **注意:此文发在 CACM 50(5) 94-101,不是 SIGMOD Record**(用户记忆有误,SIGMOD Record 是同作者更早的数据库版综述线) | [ResearchGate](https://www.researchgate.net/publication/220425814_Accessing_the_deep_Web_A_survey) / [Semantic Scholar](https://www.semanticscholar.org/paper/ab0b97987102c9b67b78164d47e9cadcd3dd3d01) | 一手(综述) |
| **Barbosa & Freire, "An Adaptive Crawler for Locating Hidden-Web Entry Points"(WWW 2007);另见 CIKM 2004 "Building a Focused Crawler for the Hidden Web"** | 爬虫在抓取过程中**自适应学习**哪些查询词/链接模式有前景,随抓取进行动态调整焦点,减少无效请求 | 自适应查询选择的经典出处;CIKM 2004 版正是"从采样页面提取属性做查询词"的鼻祖 | [Semantic Scholar](https://www.semanticscholar.org/paper/dac8c78a55cf89802573ef972fbfeaef61cfd384) / [ACM PDF](https://dl.acm.org/doi/pdf/10.1145/1242572.1242632) | 一手(论文) |
| 补充:Olston & Najork "Web Crawling"(FnTIR 2010)权威综述,含 hidden-web 章节可作总览 | — | 快速建立全局地图用 | [PDF](https://www.ccs.neu.edu/home/vip/teach/IRcourse/IR_surveys/olston-najork@web-crawling10-crop.pdf) | 一手(综述) |

## 2. 覆盖度/规模的估计方法(对症我们"不知道还剩多少")

我们手上**已经有**做估计需要的全部数据:163 个词各自命中的文档 ID 集合。一个文档被几个词命中 = 它被"捕获"了几次——这正是 capture-recapture 的输入。

| 方法 | 核心公式/思想 | 怎么用于估计"还剩多少" | 链接 | 等级 |
|---|---|---|---|---|
| **Lawrence & Giles, "Accessibility of Information on the Web"(Nature 1999)** | 两个独立搜索引擎的**重叠率**做 capture-recapture:|A∩B|/|A| 估计 B 相对 A 的规模 | 两份独立词表的并集 vs 交集可直接算;其思想是一切后续工作的源头 | [Penn State PDF](https://clgiles.ist.psu.edu/papers/Nature-99.pdf) / [ACM](https://dl.acm.org/doi/pdf/10.1145/333175.333181) | 一手(实证) |
| **Chao(1987)+ Lee & Chao(1994)→ Chao92 / sample coverage 家族** | 猎物可捕获性不均时的修正:Chao1 = D + f1²/(2f2)(f1=只被捕获一次的个体数,f2=两次);sample coverage Ĉ = 1 − f1/n | **把 163 个词当 163 次"捕捞"**:统计每个文档被几个词命中 → f1=仅 1 个词命中的文档数,f2=2 个词命中的文档数 → 估计宇宙总量 N̂ = D + f1²/(2f2);若词捕获概率严重不均(宽词 vs 领域词确实如此),用 Chao92(sample-coverage 修正)代替 Chao1 | Chao 1987 [PDF](https://gwern.net/doc/statistics/order/capture/1987-chao.pdf);Lee & Chao 1994 [PubMed](https://pubmed.ncbi.nlm.nih.gov/19480084/) | 一手(统计理论) |
| **Chao & Jost, "Coverage-based rarefaction and extrapolation"(Ecology 2012)+ iNEXT 包** | 按样本覆盖度(而非样本量)标准化;可外推"再花多少努力才能到 95% 覆盖" | iNEXT 直接吃 frequency-of-frequencies 向量(f1,f2,f3…),输出**覆盖度曲线+外推**:回答"163 词达到约 X% 覆盖,再加 100 词约到 Y%"——这正是我们要的停止判据 | [ESA](https://esajournals.onlinelibrary.wiley.com/doi/10.1890/11-1952.1) / [iNEXT CRAN](https://cran.r-project.org/web/packages/iNEXT/refman/iNEXT.html) | 一手(方法+软件) |
| **Dasgupta, Das, Mannila, "A Random Walk Approach to Sampling Hidden Databases"(SIGMOD 2007)** | 把查询结果当图节点做随机游走,从查询接口抽出**近均匀样本** | (注:发在 SIGMOD 2007,非 UAI)给出"无偏抽样"原语;抽样后样本内重复率即可推总量 | [DBLP/ACM](https://dl.acm.org/doi/10.1145/1247480.1247555) / [dblp](https://dblp.org/pid/m/HMannila) | 一手(论文) |
| **Wang, Zhang, Chang, Prabhakar, "Unbiased Estimation of Size and Other Aggregates over Hidden Web Databases"(SIGMOD 2010)** | 用少量精心设计的查询通过受限接口**无偏估计数据库总量**,控制方差 | 更接近"正面硬算 N"的路线;但假设查询接口可控,适配度低于 Chao 系 | [ACM DL](https://dl.acm.org/doi/10.1145/1807167.1807259) | 一手(论文) |
| **Lu, "Estimating Deep Web Data Source Size by Capture-Recapture Method"** | 把 capture-recapture 直接用于 deep web 数据源:重叠率→规模 | 与我们场景几乎逐字对应 | [PDF](https://jlu.myweb.cs.uwindsor.ca/irj.pdf) | 一手(论文) |
| **Bar-Yossef & Gurevich, "Random Sampling from a Search Engine's Index"(WWW 2006,JACM 2008 扩展)** | 修正关键词采样偏差,给出搜索引擎索引的近均匀抽样原语(后续用于索引规模测量) | 提醒我们:按词聚合天然有偏差(热门词文档更易被抓),估计时需加权修正 | [ACM](https://dl.acm.org/doi/10.1145/1411509.1411514) / [Google PDF](https://research.google.com/pubs/archive/35211.pdf) | 一手(论文) |
| **Good-Turing / 物种累积曲线** | Good(1953):单例比例 f1/n 估计未见比例;物种累积曲线新物种增速→0 即近饱和 | 最便宜的工程版:**画"每新增一个词带来多少新文档"的累积曲线**,平台化即近饱和;f1/n(仅被一个词命中的文档占比)是"还剩多少"的一阶估计 | Good 1953 原始思想见 [Chao 2023 综述性论文 PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC9828287/) | 一手(理论) |
| **工程实践:Crawl4AI Adaptive Strategies(官方文档)** | 明确实现了**饱和停止判据**:"new pages stop providing novel information… rapidly diminishing returns" 即停 | 开源工程界已把"新页边际增益"做成现成停止判据,可直接借鉴其阈值设计 | [docs.crawl4ai.com](https://docs.crawl4ai.com/advanced/adaptive-strategies/) | 一手(官方文档) |
| Google "Crawl Budget Management"(官方) | 爬虫侧以边际价值管理预算,soft-404/参数页视为浪费 | 印证"边际新文档率"是工业界共识信号 | [developers.google.com](https://developers.google.com/crawling/docs/crawl-budget) | 一手(官方文档) |

**映射到本项目(Chao92 需要什么数据,一步步):**

1. 数据现成:对每篇已落盘文档,统计它被 163 个词中几个词命中(词-文档倒排表已可从去重日志重建);
2. 计算 f_k = 恰被 k 个词命中的文档数;D = 文档总数,n = 163(或用总捕获次数 Σk·f_k);
3. 粗估:Chao1:N̂ = D + f1²/(2f2);sample coverage:Ĉ = 1 − f1/n;
4. 精估:直接把 (f1…f163 词数分布) 喂给 iNEXT(R 一行),得覆盖率+外推曲线;f2=0 或极小时估计无置信度(诚实边界:宽词深处仍在贡献新文档说明 f1 比例高,Ĉ 会显著 <1);
5. 诚实边界(必须写进结论):Chao 系假设各次捕获**独立**;我们的词高度相关(同族词共享结果),相关性会使 f1 偏高 → N̂ 偏高、Ĉ 偏低(保守方向,可接受);另外深分页被钳制意味着某些词"理论上够得着但抓不全",估计的是"该接口可枚举宇宙"而非全库。

## 3. 查询生成的改进策略(比手工 163 词更聪明)

| 策略 | 出处/思想 | 映射到本项目 | 链接 | 等级 |
|---|---|---|---|---|
| **Snowball/bootstrapping:从已抓文档自动提词** | Ntoulas 2005(siphoning 内核:采样→提词→估收益→贪心);Barbosa & Freire CIKM 2004(从采样页面提取属性做查询) | 把"标题高频名词"从一次性离线步骤改成**滚动闭环**:每轮抓完,统计新文档标题的 n-gram,减去已用词表,候选池自动增长;宽词深处页面仍在贡献新文档 ⇒ 词表远未收敛,snowball 优先级最高 | [ACM](https://dl.acm.org/doi/10.1145/1065385.1065407) / [CIKM04 引文](https://www.semanticscholar.org/paper/dac8c78a55cf89802573ef972fbfeaef61cfd384) | 一手 |
| **贪心边际增益 + set cover 视角** | Ntoulas 2005 的 query reward;He, Xin, Ganti et al., "Crawling Deep Web Entity Pages"(WSDM 2013):预算约束下选词最大化新实体数,提出 **q-unit** 成本单位与 **tail-focused query selection**(专挑预计返回稀疏/罕见结果的词,而不是返回多的词) | 每轮给候选词打分 = 预期新文档数 ÷ 预期请求数(请求预算 1/s、单轮 ≤7500);**"返回巨多的宽词"边际增益其实低**——把每词前 N 页的新文档率作为该词继续翻页与否的判据,即 tail-focused 思想 | [作者 PDF](https://www.biz.uiowa.edu/faculty/nstreet/he11.pdf) / [引文记录](https://cyber-trust.eu/wp-content/uploads/2020/02/D5.1.pdf) | 一手 |
| **分类属性做分区键** | Madhavan 2008(表单字段=天然分区);He et al. 2007 综述(按属性值切分空间) | 已有 productIds;可再加:**时间窗**(sortsType=2 按时间倒序 → 按年/季切窗,可绕单查询 2500 条钳制,这是深分页钳制的标准破解法)、**语言**(中/英已分离)、**实体/模块词**(字段名、报错码、菜单路径等标题 token 当维度切)、文档类型参数(若有) | [Google PDF](https://research.google.com/pubs/archive/34618.pdf) | 一手 |
| Liakos & Ntoulas, "Topic-Sensitive Hidden-Web Crawling"(WISE 2012,注:非 WWW 2006) | 给查询选择加主题约束,预算内最大化主题相关覆盖 | 若只要两个产品线的语料,相关性过滤应进查询选择目标函数而非事后清洗 | [PDF](https://cgi.di.uoa.gr/~antoulas/pubs/ntoulas_topic_hw.pdf) | 一手 |

## 4. AI/LLM 时代的增量(2023+)

| 工作 | 与"语料枚举"的关系 | 链接 | 等级 |
|---|---|---|---|
| **"Knowledge-Driven Seed Generation via LLMs for Deep Web Crawling"(MDPI Applied Sciences, 2025)** | **最对症**:用 LLM+知识库自动生成 deep web 抓取种子查询,替代人工词表 | [mdpi.com](https://www.mdpi.com/2076-3417/15/19/10396) | 一手(实证) |
| **CRAW4LLM: Efficient Web Crawling for LLM Pretraining**(ACL Findings 2025) | 用 LLM 打分优先爬高质量内容——方向是"为 LLM 爬"而非"用 LLM 枚举",但爬取优先级思想可借 | [arXiv 2502.13347](https://arxiv.org/html/2502.13347v1) | 一手(实证) |
| **Query Expansion in the Age of Pre-trained and Large Language Models**(2025 综述) | LLM 查询扩展全景;含 corpus-steered query expansion(用语料统计引导 LLM 扩词)——正是"163 词 + LLM 候选池"的学术框架 | [arXiv 2509.07794](https://arxiv.org/pdf/2509.07794) | 一手(综述) |
| Neural Prioritisation for Web Crawling(2025) | 神经 best-first 爬取策略 + LLM 质量估计 | [arXiv 2506.16146](https://arxiv.org/html/2506.16146v1) | 一手(实证) |
| Coverage-Aware Web Crawling for Domain-Specific Data(2026) | 领域定向爬取的覆盖度感知策略(题名即对症,细节待读) | [arXiv 2602.24262](https://arxiv.org/abs/2602.24262) | 一手(待核) |

诚实边界:2023 后**没有**发现把 LLM 用于"关键词接口全库枚举+覆盖度估计"闭环的成体系论文;LLM 增量集中在(a)种子/扩展词生成、(b)爬取优先级。把 LLM 塞进"提词→估边际增益"闭环,是对经典 siphoning 的低成本升级,不是范式替换。

## 5. 落地清单(按性价比排序)

1. **先算覆盖率(零请求成本)**:重建词-文档倒排 → f1/f2 → Chao1 与 Ĉ=1−f1/n;iNEXT 出外推曲线,把"还剩多少"变成有置信区间的数;
2. **词表滚动化**:每轮落盘后从新文档标题提 n-gram 进候选池(已做一次的事,改成每轮做);
3. **边际增益调度**:候选词按"上轮每页新增文档数"排序,低于阈值的词停翻、低于全词阈值的词淘汰;时间窗分区( sortsType=2 下的按年切片)用于绕 2500 钳制;
4. **可选 LLM 化**:候选池枯竭时用 LLM 基于已抓标题批量生成组合词(错误码/功能名×动作词),仍以边际增益验证后才进词表。

## 6. 参考来源

- [Ntoulas et al., JCDL 2005(siphoning)](https://dl.acm.org/doi/10.1145/1065385.1065407)
- [Madhavan et al., VLDB 2008(Google's Deep Web Crawl)](https://research.google.com/pubs/archive/34618.pdf) / [ACM](https://dl.acm.org/doi/10.14778/1454159.1454163)
- [He et al., CACM 2007(Deep Web survey)](https://www.researchgate.net/publication/220425814_Accessing_the_deep_Web_A_survey)
- [Barbosa & Freire, WWW 2007(adaptive crawler)](https://dl.acm.org/doi/pdf/10.1145/1242572.1242632)
- [He et al., WSDM 2013(Crawling Deep Web Entity Pages, q-unit)](https://www.biz.uiowa.edu/faculty/nstreet/he11.pdf)
- [Dasgupta, Das, Mannila, SIGMOD 2007(random walk sampling)](https://dl.acm.org/doi/10.1145/1247480.1247555)
- [Wang et al., SIGMOD 2010(unbiased size estimation)](https://dl.acm.org/doi/10.1145/1807167.1807259)
- [Lawrence & Giles, Nature 1999](https://clgiles.ist.psu.edu/papers/Nature-99.pdf)
- [Chao 1987(capture-recapture 不等捕获性)](https://gwern.net/doc/statistics/order/capture/1987-chao.pdf) / [Lee & Chao 1994(sample coverage)](https://pubmed.ncbi.nlm.nih.gov/19480084/)
- [Chao & Jost 2012(coverage-based rarefaction)](https://esajournals.onlinelibrary.wiley.com/doi/10.1890/11-1952.1) / [iNEXT](https://cran.r-project.org/web/packages/iNEXT/refman/iNEXT.html)
- [Bar-Yossef & Gurevich, WWW 2006(搜索索引随机采样)](https://dl.acm.org/doi/10.1145/1411509.1411514)
- [Lu(deep web capture-recapture)](https://jlu.myweb.cs.uwindsor.ca/irj.pdf)
- [Crawl4AI Adaptive Strategies(饱和停止判据工程实现)](https://docs.crawl4ai.com/advanced/adaptive-strategies/) / [Google Crawl Budget](https://developers.google.com/crawling/docs/crawl-budget)
- [MDPI 2025(LLM 种子查询生成)](https://www.mdpi.com/2076-3417/15/19/10396) / [CRAW4LLM](https://arxiv.org/html/2502.13347v1) / [QE 综述 arXiv 2509.07794](https://arxiv.org/pdf/2509.07794) / [arXiv 2506.16146](https://arxiv.org/html/2506.16146v1)
- [Olston & Najork, Web Crawling 综述](https://www.ccs.neu.edu/home/vip/teach/IRcourse/IR_surveys/olston-najork@web-crawling10-crop.pdf)
