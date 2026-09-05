---
name: kingdee-knowledge
description: >-
  检索金蝶官方知识库(匿名免费:零账号/零点数/无 LLM 生成/无限流)。
  当用户询问金蝶产品相关问题——金蝶云·星空/旗舰版、苍穹、星空企业版/标准版的
  配置方法、操作步骤、报错排查、字段/API 说明——时,用 kd CLI 搜索官方知识库
  (含社区问答帖)、读取全文后带引用回答。
---

# 金蝶知识库检索(kingdee-knowledge)——kd CLI 流程

本地「金蝶知识检索服务」(默认 127.0.0.1:4097,逆向自金蝶云社区官方后端)的命令行入口 `kd`,
**纯匿名:零账号、零点数、无澄清、无对话限流、零 LLM**。默认输出 JSON(stdout=数据,stderr=进度)。

## kd 的定位(三级回退)

1. PATH 里的 `kd`
2. Windows: `%USERPROFILE%\.kingdee-kit\bin\kd.cmd`；Linux/macOS: `~/.kingdee-kit/bin/kd`
3. 都没有 → 告知用户运行本仓库安装器(install.ps1 / install.sh);**在此之前不要编造任何金蝶知识内容**

能力清单:`kd manifest`(端点/参数/示例的机器可读文档,拿不准就先看它)

## ⭐ 标准流程:检索 → 按类型读全文 → 带引用回答

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

**第 2 步:读全文(按类型选命令)**

```bash
kd article <id>                    # knowledge 条目 → 官方文档全文
kd question <questionId>           # answer 条目 → 问题+全部回答+追问链(采纳优先)
kd article <id> --kind article     # article 条目 → 社区文章全文
kd answer <id>                     # 单条回答全文(极少用,question 已含全部回答)
```

**第 3 步:回答**

- 整合全文后回答,**附官方 url**;问答帖内容标注"来自社区"
- 区分"官方文档确认的"与"社区经验/推测"

## 快捷方式

- `kd ask "问题" --topk 4` —— 一站式资料包:检索+深读 topK 全文一次带回,你(当前 AI)拿包
  直接合成带引用回答,不必多轮 search+article
- `kd share <官方分享短链>` —— 读官方 AI 分享对话全文(含引用),可作为高质量参考

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
