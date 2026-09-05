---
name: kingdee-knowledge
description: >-
  检索金蝶官方知识库(匿名免费:零账号/零点数/无限流)。当用户询问金蝶产品相关问题——
  金蝶云·星空/旗舰版、苍穹、星空企业版/标准版的配置方法、操作步骤、报错排查、字段/API 说明——时,
  用 kd CLI 搜索官方知识库(官方文档+社区问答+社区文章)、读取全文后按 ANSWER-SPEC 带引用回答;
  有模型通道时也可 kd ai 一步合成。
---

# 金蝶知识库检索(kingdee-knowledge)——kd CLI 流程

本地「金蝶知识检索服务」(默认 127.0.0.1:4097,逆向自金蝶云社区官方后端)的命令行入口 `kd`,
**纯匿名:零账号、零点数、无澄清、无对话限流**。默认输出 JSON(stdout=数据,stderr=进度)。

## kd 的定位(三级回退)

1. PATH 里的 `kd`(Git Bash 下裸名走无扩展名 shim;cmd/PowerShell 走 kd.cmd)
2. Windows: `%USERPROFILE%\.kingdee-kit\bin\kd.cmd` 或 `%USERPROFILE%\.lingeebuild\bin\kd.cmd`;
   Linux/macOS: `~/.kingdee-kit/bin/kd`
3. 都没有 → 告知用户运行本仓库安装器(install.ps1 / install.sh);**在此之前不要编造任何金蝶知识内容**

能力清单:`kd manifest`(命令/端点/参数的机器可读文档,拿不准就先看它)

⚠️ **永远不要把 stderr 丢弃(禁 `2>/dev/null`)**:stdout=数据,stderr=进度与诊断;命令失败时先看
stderr/错误 JSON(带 hint),据此换调用方式,而不是静默改道 websearch。

## ⭐ 标准流程:检索 → 照抄 type 读全文 → 按 ANSWER-SPEC 回答

**第 1 步:检索**(三种实体全返回,`type` 字段区分)

```bash
kd search "信用额度控制" --product 93 --size 10
# → {"total","results":[{type,id,questionId?,title,questionBody?,snippet,adopted?,url,…}]}
```

- **关键词是准确性关键**:把用户问题转写为具体的功能名/业务名词/报错词;搜不到就换词;
  复杂问题拆 2-3 个关键词分别搜(或 `kd ask --kw "词1" --kw "词2"`)
- `--product` 路由:93=星空旗舰版(默认)、87=苍穹、1=星空企业版/标准版、0=不过滤
- `--type knowledge|answer|article` 可过滤;`knowledge`=官方文档(权威优先)、
  `answer`=社区问答帖(实战报错/方案,条目内联 questionId)、`article`=社区文章

**第 2 步:读全文**——`--kind` 直接照抄 search 结果的 `type` 字段,零翻译:

```bash
kd read <id>                    # type=knowledge → 官方文档全文(默认 kind)
kd read <questionId> --kind answer    # type=answer → 问题+全部回答+追问链(传条目的 questionId)
kd read <id> --kind article           # type=article → 社区文章全文
```

**第 3 步:回答**——遵循回答规范 `docs/ANSWER-SPEC.md`(单一事实源):结构化 Markdown
(问题原因分析 → 解决方案编号步骤 → 操作边界),表格、正文 `[n]` 编号引用 + 文末「参考来源」列表;
资料未覆盖就诚实声明,**不编造菜单路径/字段名/接口名**;社区内容标注「来自社区经验」。

## 快捷方式

- `kd ask "问题" --topk 4` —— 一站式资料包:检索+深读 topK 全文一次带回,你拿包直接按
  ANSWER-SPEC 合成,不必多轮 search+read
- `kd ai "问题"` —— 语义识别→检索→一步合成带引用回答(KAI_BASE/KAI_MODEL 指向你的
  OpenAI 兼容端点);返回带 `fallback:true` 表示模型通道不可用、已自动降级为资料包,
  此时你拿 `sources` 自己按 ANSWER-SPEC 合成即可
- `kd share <官方分享短链>` —— 读官方 AI 分享对话全文(含引用),可作高质量参考与官方效果样本

## 服务不可用时

```bash
kd health    # 错误 JSON 的 hint 字段里有修复指引
```

仍不行才回退 websearch/webfetch(`site:vip.kingdee.com`、help.open.kingdee.com),并告知用户。

## 禁止事项

- 不要编造端点/字段/参数;内容以 kd 返回的 JSON 为准
- 不要把检索失败当成"官方没有相关文档"——先换关键词再搜
- 不要高频连续检索(保持人类频率)
- 不要尝试官方 /aisapi/ai-search 管线——需登录+身份认证,匿名只会得到「未授权操作」
