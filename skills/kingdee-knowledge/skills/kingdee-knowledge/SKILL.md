---
name: kingdee-knowledge
description: >-
  检索金蝶官方知识库(匿名免费:零账号/零点数/无限流)。当用户询问金蝶产品相关问题——
  金蝶云·星空/旗舰版、苍穹、星空企业版/标准版的配置方法、操作步骤、报错排查、字段/API 说明——时,
  用 kd CLI 搜索官方知识库(官方文档+社区问答+社区文章)、读取全文后按 ANSWER-SPEC 带引用回答;
  有模型通道时也可 kd ai 一步合成。
---
# 金蝶知识库检索(kingdee-knowledge)——kd CLI 流程

本地「金蝶知识检索服务」(默认 127.0.0.1:4097,v5.0,逆向自金蝶云社区官方后端)的命令行入口 `kd`,
**纯匿名:零账号、零点数、无澄清、无对话限流**。默认输出 JSON(stdout=数据,stderr=进度)。

## kd 的定位(三级回退)

1. PATH 里的 `kd`(Git Bash 下裸名走无扩展名 shim;cmd/PowerShell 走 kd.cmd)
2. Windows: `%USERPROFILE%\.kingdee-kit\bin\kd.cmd` 或 `%USERPROFILE%\.lingeebuild\bin\kd.cmd`;
   Linux/macOS: `~/.kingdee-kit/bin/kd`
3. 都没有 → 告知用户运行本仓库安装器(install.ps1 / install.sh);**在此之前不要编造任何金蝶知识内容**

能力清单:`kd manifest`(命令/端点/参数的机器可读文档,拿不准就先看它)

⚠️ **永远不要把 stderr 丢弃(禁 `2>/dev/null`)**:stdout=数据,stderr=进度与诊断;命令失败时先看
stderr/错误 JSON(带 hint),据此换调用方式,而不是静默改道 websearch。

## ⭐ 信源排序链(每类资料的固定检索顺序,不要临场发挥)

| 资料类型 | 检索链(按优先级) |
|---|---|
| **社区问答** | ① `rg` corpus/answer(零上游)→ ② `kd search --type answer` + `kd read <questionId> --kind answer` |
| **官方文档** | ① `rg` corpus/knowledge → ② `kd search --type knowledge` + `kd read` → ③ 帮助中心站外兜底 `site:help.open.kingdee.com`(websearch,标注「站外语源」) |
| **社区文章** | ① `rg` corpus/article → ② `kd search --type article` + `kd read` |
| **发版说明(更新日志)** | ① `rg` corpus(词表含"发版说明")→ ② `kd search` + `kd read` 按 ID ③ 站外兜底 `site:vip.kingdee.com` 发版说明;诚实边界:部分发版说明不在搜索索引内,搜不到≠不存在 |
| **课程** | ① websearch `site:vip.kingdee.com` 课程页 → ② 接入中(逆向立项);回答必须标注「课程/站外语源」 |

通用原则:rg 命中即读文件、引用 front-matter 的原链接;`stub: true` 只有摘要,需要正文就 `kd read`(自动写穿覆盖);在线检索只在本地无命中或需要更多线索时进行。

## ⭐ 检索策略:先 rg 本地 corpus(零成本),再 kd ask(在线)

**第 0 步:`rg` 直接搜本地语料目录**(每次深读/发现层落盘的语料,一文档一 md,带原链接 front-matter):

```bash
rg -il --no-messages "BOM 分母" ~/.lingeebuild/corpus        # 文件名列表
rg -i --no-heading -m 3 "信用额度" ~/.lingeebuild/corpus      # 匹配行(带文件路径,可连续读多个)
```

- corpus 路径以 `kd health` 的 `corpus.path` 为准(默认 `~/.lingeebuild/corpus`);无 rg 时退 ugrep/grep(-r,能力弱化可接受);
- 命中即读文件正文(front-matter 有原链接,回答必须引用);`stub: true` 的文件只有摘要,
  需要正文就 `kd read <id> --kind <type>`(会写穿覆盖 stub);
- corpus 没有或太少(正常,覆盖率靠发现层积累)→ 走在线链路;

**检索词改写(口语→术语,由你弥补语义鸿沟——这是 grep 路线的核心,不要偷懒直接搜原句)**:

1. 症状词→功能名词:「数量翻倍/变成平方」→「MRP 需求用量 分子分母」;「锁单」→「信用额度 控制」;
2. 每个问题准备 **2-3 组关键词**(功能名/字段名/报错文案/单据名),一组没中换下一组;
3. 产品路由:`--product 93`=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、0=不过滤。

**第 1 优先:`kd ask`**(一站式资料包,多数问题一步够用,不要先手动多轮 search):

```bash
kd ask "BOM分母27000 MRP运算变成平方" --topk 4
# → sources[]:每源含 title/url/detail 全文/chunks(标题感知切片 top3 相关段,可引用 [n](chunk#m))
# 多关键词拆解:kd ask --kw "BOM 分母" --kw "MRP 用量"(服务端自动 RRF 融合)
```

**有模型通道时:`kd ai`**(关键词规划→检索→按 ANSWER-SPEC 合成带引用回答,一步出答案;
不可用自动降级资料包 `fallback:true`,此时你拿 sources 自己按 ANSWER-SPEC 合成):

```bash
kd ai "BOM分母27000 MRP运算变成平方" --topk 4
```

**第 2 步(ask 无命中或需更多线索):search → read**

```bash
kd search "信用额度控制" --product 93 --size 10
# → {"total","results":[{type,id,questionId?,title,snippet,adopted?,url,…}]}
# --type knowledge|answer|article 可过滤;knowledge=官方文档(权威优先)
```

读全文——`--kind` 照抄 search 结果的 `type` 字段,零翻译:

```bash
kd read <id>                          # type=knowledge → 官方文档全文
kd read <questionId> --kind answer    # type=answer → 问题+全部回答+追问链
kd read <id> --kind article           # type=article → 社区文章全文
```

回答遵循 `docs/ANSWER-SPEC.md`(单一事实源):结构化 Markdown(原因分析→编号步骤→操作边界)、
表格、`[n]` 编号引用(可细到 `[n](chunk#m)`)、资料未覆盖就诚实声明,**不编造菜单路径/字段名/接口名**;
社区内容标注「来自社区经验」;corpus 命中的回答引用原链接(front-matter `url:`)。

## usage 沉淀(每次真实解题会话收尾必做)

会话结束前,把「问题 + 实际起作用的文档」写进 corpus 的 usage 目录
(`<corpus>/usage/YYYY-MM-DD-<主题slug>.md`),格式:

```markdown
---
type: usage
date: 2026-09-06
question: <用户问题原话>
---
## 解题文档
- [标题](原链接)(type/来源)
## 要点
<一句话:什么症状、哪个文档解决了>
```

这是评测集 usage 层的生长源(先标金、后测管线),也是发现层的种子。

## 管线与本地存储(v5)

- **corpus 语料目录**:read/ask 深读同步写穿全文;时间网格(手动跑 `scripts/discovery_sweep.py`)与
  官方分享引用自动落 stub;`rg` 直接搜,机器缓存与人读语料分离;
- **sqlite 上游缓存**(用户本机默认开):检索/深读结果自动沉淀本地 sqlite,重复查询毫秒级返回、不打上游;
  v5 已缩编为纯上游缓存,FTS5/chunks/`local=1` 冻结开发(deprecated,检索角色由 corpus+rg 接管);
- 上游是人类频率红线:**多用缓存与 corpus,少做重复在线检索**;
- **评测口径已脱离官方 AI 对话**:usage 层(真实使用的问题→解题文档对)是唯一达标金标;
  官方分享短链仅作参考素材(`kd share` 能力保留)。

## 快捷方式

- `rg "关键词" ~/.lingeebuild/corpus` —— 本地语料直搜(零上游,首选)
- `kd ask "问题" --topk 4` —— 一站式资料包:检索+深读 topK 全文一次带回(在线首选)
- `kd ai "问题"` —— 一步合成带引用回答(KAI_BASE/KAI_MODEL 指向 OpenAI 兼容端点)
- `kd share <官方分享短链|chatId>` —— 读官方 AI 分享对话全文(引用自动沉淀 corpus)

## 服务不可用时

```bash
kd health    # 错误 JSON 的 hint 字段里有修复指引
```

仍不行才回退 websearch/webfetch(`site:vip.kingdee.com`、help.open.kingdee.com),并告知用户。

## 禁止事项

- 不要编造端点/字段/参数;内容以 kd 返回的 JSON / corpus 文件正文为准
- 不要把检索失败当成"官方没有相关文档"——先改写关键词(2-3 组)再搜
- 不要高频连续检索(保持人类频率;缓存命中与 rg 本地搜不受限)
- 不要尝试官方 /aisapi/ai-search 管线——需登录+身份认证,匿名只会得到「未授权操作」
