# 调研:AI 时代的 grep —— kd 该不该自建向量库?(2026-09-06)

> 起因:用户判断「RAG 太重、个人使用者成本高」,倾向 grep 式词法检索。
> 方法:一手来源=arXiv 论文原文+Anthropic 公开表态;按 kd 语料的**文档类型逐类判断**。

## 1. 「AI 时代的 grep」辩论图谱(带证据等级)

| 立场 | 证据 | 等级 |
|---|---|---|
| **Anthropic 试过向量,扔了,上线 grep+glob+read**(Claude Code 负责人 Boris Cherny) | [LinkedIn 转述](https://www.linkedin.com/posts/ritvik-rastogi-003085153_ive-spent-a-lot-of-time-building-rag-pipelines-activity-7462345677625479168-USu6) | 一手转述 |
| **PwC 论文《Is Grep All You Need?》**:检索×harness×交付方式是"一个联合评估的系统" | [arXiv 2605.15184](https://arxiv.org/html/2605.15184v1) | 一手(实证) |
| **反方**:harness 效应可能大于检索器本身(Jerry Liu, LlamaIndex);BM25/grep 并非永远够(Aaron Tay) | [LinkedIn](https://www.linkedin.com/posts/jerry-liu-64390071_theres-an-open-question-on-whether-grep-activity-7461843399428440064-Cnxb) / [substack](https://aarontay.substack.com/p/what-changes-when-an-llm-agent-searches) | 一手(观点) |
| **agentic search 栈替代 RAG**:Windsurf SWE-grep 10 倍提速;终端工具直接搜原文 | [Medium 综述](https://buzzgrewal.medium.com/ai-agents-dont-need-vector-search-anymore-inside-the-agentic-search-stack-replacing-rag-in-2026-58efcabe4f6f) / [VentureBeat DCI](https://venturebeat.com/orchestration/your-ai-agents-need-a-terminal-not-just-a-vector-database) | 二手 |
| 中文社区 | 未检索到相关方法论文章(kd 搜索命中的均为产品功能帖) | 缺口 |

## 2. PwC 论文的胜负条件(最有分量的一手)

- **grep 全胜区:inline 内联工具调用** —— "inline grep exceeds inline vector for every harness-model pair"(最大差距 86.2% vs 62.9%);
- **向量反超区:programmatic 文件交付**(10 组配对赢 5 组)+ 干扰会话少的小语料;
- **适用条件**:LongMemEval 奖励「字面片段还原」(literal witnesses)时 grep 占优;证据是转述/概念性时向量或混合更合适;
- **核心警告**:换 harness 的影响与换检索器相当——检索器不能脱离 agent 脚手架单独评判;
- **明确限定**:结论只基于多会话对话 QA,外推到企业知识库需自行验证(Limitations 原文)。

## 3. 按官方参考文章类型逐类判断(grep 够不够用)

kd 的 harness = ZCode agent + kd CLI,**inline 内联调用**(我调 kd、读 JSON、迭代换词)——恰好落在论文 grep 全胜区的条件上。语料语言特征:ERP 域术语高度标准化(功能名/字段名/报错文案/版本号 = literal witnesses)。

| 文档类型 | 查询↔文档措辞关系 | grep(词法)够用? | 依据 |
|---|---|---|---|
| 社区问答(answer) | 问题标题=用户症状原话,查询即症状 | ✅ **强项** | 字面高精度命中,论文 inline 条件 |
| 发版说明 | 版本号/【新功能】/功能名,症状与修复描述共享域名词 | ✅ 够用 | literal witnesses;需发现层先覆盖 |
| 手册型知识/库内产品文档 | 菜单路径/字段名为主,口语查询有改写空间 | ✅ 基本够用 | agent 多路改写补口语→术语鸿沟 |
| 社区文章(article) | 技术术语密集 | ✅ 够用 | 同上 |
| 课程(school) | 文本(字幕/讲义)未在我们语料 | ⏳ N/A | 先解决信源,再谈检索方式 |
| 帮助中心产品文档 | 站外信源 | ⏳ N/A | 同上 |
| **真正的瓶颈:官方金标 0/10 可搜** | 与检索方式**无关**——索引不收录,词法/语义都空转 | — | 昨日实证(调研报告 §6):图游走+时间网格才是对症药 |

## 4. 结论

**grep(词法)+ agent 改写,对我们够用——向量库二期降级为「墙到再上」。**

1. 我们满足 grep 全胜的两个条件:inline agent harness + 术语密集/literal-witness 型语料;
2. 口语→术语的语义鸿沟由 **agent 本身**弥补(多路改写/换词迭代)——这正是论文说的 harness effect,零基础设施成本,比我上一轮被评测否决的手工信号重排更聪明(语义工作交给 LLM 而不是权重表);
3. **当前真正的墙是发现层**(官方金标 0/10 在搜索索引内),与 grep-vs-vector 无关:图游走(recommendArray BFS)+ sortsType=2 时间网格是正解,已列改进清单第一位;
4. **不做丢弃**:chunks 表 embedding BLOB 列与评测门保留——若未来 usage 层撞上「改写多轮仍搜不到」的转述型长尾,再以评测数据立项向量,成本与决策依据都在。

## 5. 参考来源

- [Is Grep All You Need? How Agent Harnesses Reshape Agentic Search(PwC, arXiv 2605.15184)](https://arxiv.org/html/2605.15184v1)
- [Beyond Semantic Similarity: Rethinking Retrieval for Agentic Search(arXiv 2605.05242)](https://www.alphaxiv.org/abs/2605.05242)
- [Anthropic grep+glob+read 表态(LinkedIn)](https://www.linkedin.com/posts/ritvik-rastogi-003085153_ive-spent-a-lot-of-time-building-rag-pipelines-activity-7462345677625479168-USu6)
- [Jerry Liu harness 质疑(LinkedIn)](https://www.linkedin.com/posts/jerry-liu-64390071_theres-an-open-question-on-whether-grep-activity-7461843399428440064-Cnxb) / [Aaron Tay 平衡观点](https://aarontay.substack.com/p/what-changes-when-an-llm-agent-searches)
- [BM25, Embeddings, and the Power of Agentic Search(rajivshah.com)](https://rajivshah.com/blog/rag-agentic-world.html) / [VentureBeat: agents need a terminal](https://venturebeat.com/orchestration/your-ai-agents-need-a-terminal-not-just-a-vector-database)
- 本项目:调研报告 §6(发现层实证)、eval-report-v4.md、交接文档 16
