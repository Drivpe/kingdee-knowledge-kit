---
name: kingdee-knowledge
description: >-
  检索金蝶官方知识库(匿名免费:零账号/零点数/无限流)。当用户询问金蝶产品相关问题——
  金蝶云·星空/旗舰版、苍穹、星空企业版/标准版的配置方法、操作步骤、报错排查、字段/API 说明——时,
  常规问题一律先 `kd ask`(内置多路关键词拆解,一站式带回资料包),再按 ANSWER-SPEC 带引用回答;
  有模型通道时也可 kd ai 一步合成。rg 只对本地落地缓存与发版说明库做字段名/报错原文级精查。
---
# 金蝶知识库检索(kingdee-knowledge)——kd CLI 流程(v6 ask-first)

本地「金蝶知识检索服务」(默认 127.0.0.1:4097,v6.1,逆向自金蝶云社区官方后端)的命令行入口 `kd`,
**纯匿名:零账号、零点数、无澄清、无对话限流**。默认输出 JSON(stdout=数据,stderr=进度)。

v6 主轴是**彻底在线**(ADR-0005):模仿人类在论坛解决问题——用时在线搜、搜到即读即查;
深读过的文档自动写穿**落地缓存**(~/.lingeebuild/landing),预囤只保留**发版说明库**。
不存在覆盖性本地语料;rg 不再是发现层入口,只做精查(见下)。

## kd 的定位(三级回退)

1. PATH 里的 `kd`(Git Bash 下裸名走无扩展名 shim;cmd/PowerShell 走 kd.cmd)
2. Windows: `%USERPROFILE%\.kingdee-kit\bin\kd.cmd` 或 `%USERPROFILE%\.lingeebuild\bin\kd.cmd`;
   Linux/macOS: `~/.kingdee-kit/bin/kd`
3. 都没有 → 告知用户运行本仓库安装器(install.ps1 / install.sh);**在此之前不要编造任何金蝶知识内容**

能力清单:`kd manifest`(命令/端点/参数的机器可读文档,拿不准就先看它)

⚠️ **永远不要把 stderr 丢弃(禁 `2>/dev/null`)**:stdout=数据,stderr=进度与诊断;命令失败时先看
stderr/错误 JSON(带 hint),据此换调用方式,而不是静默改道 websearch。

## ⭐ 检索流程(四步,不要临场发挥)

### 第 1 步:kd ask——常规问题一律此入口

一站式资料包:服务端内置**多路关键词拆解**(症状词路+字段/实体名词路+产品词路,≤6 路 RRF 融合,
规则在 service/query_routes.json)→ 并行多路搜索 → 深读 topK 全文 → 写穿落地缓存;
返回 `routes[]`(拆解明细)+ `sources[]`(每源 title/url/products/全文/chunks)+
`effectiveProductId`(本次实际生效的产品过滤)+ `budget{max,used}`。

```bash
kd ask "BOM分母27000 MRP运算变成平方" --topk 4
# --kw 可显式指定关键词(跳过自动拆解):kd ask --kw "BOM 分母" --kw "MRP 用量"
```

- **检索词改写由 ask 内置多路承担,你不再需要临场编词路**:直接把用户的症状描述原样交给 ask,
  服务端负责跨越词汇鸿沟(如「分母变平方」↔文档命名的「生产单位数量」);
- 返回体里看 `routes[]` 理解服务端拆了哪些路;`budget_exhausted:true` 表示上游预算耗尽,
  先消化已获资料,不要立刻重跑;
- sources 展示排序 answer 优先(症状近似度最高),knowledge 紧随(补根因解释)——两类都要看。

**有模型通道时:`kd ai`**(关键词规划→ask→按 ANSWER-SPEC 合成带引用回答,一步出答案;
不可用自动降级资料包 `fallback:true`,此时你拿 sources 自己按 ANSWER-SPEC 合成):

```bash
kd ai "BOM分母27000 MRP运算变成平方" --topk 4
```

### 第 2 步:rg 精查本地落地缓存与发版说明库(零上游)

rg **只对两个本地目录做字段名/报错原文级的全文精查**,不承担在线发现:

| 目录 | 内容 | 用途 |
|---|---|---|
| `~/.lingeebuild/landing` | 本次/历史会话深读写穿的文档(一文档一 md+front-matter 原链接) | 查过的毫秒级复用;**没查过的不存在**,不要期待覆盖 |
| `~/.lingeebuild/releasenotes` | 93 旗舰版近 1 年发版说明(一版本一 md,官方「模块-问题-修复」结构) | **「官方已修复」类问题的终审依据**;前两层解释不了根因时的第三跳素材 |

```bash
rg -i --no-heading -m 3 -e "生产单位数量" ~/.lingeebuild/landing        # 字段名级精查
rg -i --no-heading -m 3 -e "分母" -e "平方" ~/.lingeebuild/releasenotes  # 修复项关键词(多词必须 -e 重复,OR 语义)
rg -il -e "^title:.*追溯" ~/.lingeebuild/landing                        # 标题优先:锚定 front-matter
```

- ⚠️ **多词查询必须 `-e` 重复**(OR 语义);带空格的 `"BOM 分母"` 是字面短语,只会命中恰好连写的文本;
- 命中即读文件正文,回答引用 front-matter 的 `url:` 原链接;
- 实际路径以 `kd health` 的 `landing.path` 与安装布局为准(默认 `~/.lingeebuild/*`);
- 无 rg 时退 ugrep/grep(-r,能力弱化可接受)。

### 第 3 步:第三跳兜底——前两层空手或无法解释根因时,在线搜发版说明关键词

ask(sources)与 rg(landing+releasenotes)都空手、或拿到的资料解释不了根因时,才动用 websearch:

```bash
# 站外兜底,回答必须标注「站外语源」
site:vip.kingdee.com 发版说明 <修复项关键词>
site:help.open.kingdee.com <功能名词>
```

诚实边界:部分发版说明不在搜索索引内,搜不到≠不存在;不要因为第三跳也无果就断言「官方没有」,
按 ANSWER-SPEC 写「现有资料未覆盖」。

### 第 4 步:会话收尾——usage 沉淀

每次真实解题会话结束前,把「问题 + 实际起作用的文档」写进 usage 目录
(`~/.lingeebuild/corpus/usage/YYYY-MM-DD-<主题slug>.md`),格式:

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

这是评测集 usage 层的生长源(先标金、后测管线)。

## 手动细粒度调试命令(search/read)

仅当需要精确控制(分页/类型过滤/指定单条深读)时手动使用,常规问题不要从 search 起步:

```bash
kd search "信用额度控制" --product 93 --size 10
# → {"total","results":[{type,id,questionId?,title,snippet,adopted?,url,…}]}
# --type knowledge|answer|article 可过滤;knowledge=官方文档(权威优先)
# --product 93=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、0=不过滤
# ⚠️ --product 必须沿用本会话首次 ask 确定的路由,禁止变道 0(硬规则见下节)

kd read <id>                          # type=knowledge → 官方文档全文
kd read <questionId> --kind answer    # type=answer → 问题+全部回答+追问链
kd read <id> --kind article           # type=article → 社区文章全文
# --kind 照抄 search 结果的 type 字段,零翻译;深读自动写穿落地缓存
```

## 产品路由粘性与引用前核对(硬规则,2026-09-06 旗舰版串线事故条款)

> 事故:agent 回答「金蝶旗舰版 BOM 维护」时,中途把 `--product 93`(旗舰版过滤)变道为
> `--product 0`(不过滤),召回了星空企业版内容(ENG_BOM/T_ENG_BOM)且未甄别,
> 把企业版答案当成旗舰版输出(正确答案:旗舰版是 pdm_mftbom)。以下两条为硬规则,无例外。

1. **产品路由粘性**:同一问题会话内,首次 `kd ask` 确定的 product 路由(如 `--product 93`
   旗舰版、`--product 1` 星空企业版、`--product 87` 苍穹)**必须贯穿该会话后续所有
   search/read/ai/ask 调用**;**禁止中途变道 `--product 0`(不过滤)、禁止改用其他产品线
   过滤、禁止不带 --product 重新检索**。若用户追问中出现了新的产品线线索(如明确提到另一
   产品),**必须先向用户确认后再切换**路由,不得自行判断、不得静默切换。

2. **引用前核对产品线**:引用任何 source 前必须核对该源的 `products` 字段是否与资料包顶层
   `effectiveProductId` 一致(或包含目标产品线);**产品线不符的源禁止作为答案依据**——
   不论其相关性多高、排序多靠前,症状对齐引用也不行(混入的异产品线内容会把结论带偏)。
   资料包顶层 `effectiveProductId` 与预期不符(如要旗舰版 93 却回显 0)时,**必须停止检索
   并向用户报告,禁止带着错误过滤继续作答**。

## 回答规范

遵循 `docs/ANSWER-SPEC.md`(单一事实源):结构化 Markdown(原因分析→编号步骤→操作边界)、
表格、`[n]` 编号引用(可细到 `[n](chunk#m)`)、**根因引用规则**(answer 引用用于症状对齐,
根因解释优先引 knowledge/发版说明)、资料未覆盖就诚实声明,**不编造菜单路径/字段名/接口名**;
社区内容标注「来自社区经验」;landing/releasenotes 命中的回答引用 front-matter `url:` 原链接。

## 快捷方式

- `kd ask "问题" --topk 4` —— **唯一常规入口**:内置多路关键词拆解+深读 topK 全文一次带回
- `rg -e "词1" -e "词2" ~/.lingeebuild/landing` —— 落地缓存精查(零上游;多词必须 `-e` 重复)
- `rg -e "词1" ~/.lingeebuild/releasenotes` —— 发版说明修复项精查(「官方已修复」类问题的终审依据)
- `kd ai "问题"` —— 一步合成带引用回答(KAI_BASE/KAI_MODEL 指向 OpenAI 兼容端点)
- `kd share <官方分享短链|chatId>` —— 读官方 AI 分享对话全文(引用自动沉淀)
- `kd search` / `kd read` —— 手动细粒度调试命令(常规问题不用)

## 服务不可用时

```bash
kd health    # 错误 JSON 的 hint 字段里有修复指引
```

仍不行才回退 websearch/webfetch(`site:vip.kingdee.com`、help.open.kingdee.com),并告知用户。

## 禁止事项

- **同一问题会话内禁止把 --product 从已确定的路由变道为 0(不过滤)或其他产品线**;
  产品线切换必须先经用户确认(见「产品路由粘性与引用前核对」硬规则)
- **禁止引用 products 字段与目标产品线不符的源**;`effectiveProductId` 与预期不符时
  必须停下报告,禁止带着错误过滤继续作答(见「产品路由粘性与引用前核对」硬规则)
- 不要编造端点/字段/参数;内容以 kd 返回的 JSON / landing·releasenotes 文件正文为准
- 常规问题不要从手动 search/read 起步——一律先 kd ask;也不要在 ask 之外临场编检索词路(多路已内置)
- 不要把检索失败当成"官方没有相关文档"——先走完四步流程,再按「现有资料未覆盖」诚实声明
- 不要高频连续检索(保持人类频率;rg 精查与落地缓存命中不受限)
- 不要尝试官方 /aisapi/ai-search 管线——需登录+身份认证,匿名只会得到「未授权操作」
