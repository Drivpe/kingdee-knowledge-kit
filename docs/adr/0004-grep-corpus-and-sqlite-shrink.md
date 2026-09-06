# ADR-0004:grep 语料路线(corpus 目录);sqlite 缩编为纯上游缓存;向量二期降级待墙

日期:2026-09-06 | 状态:已采纳 | 关联:交接文档 17、docs/research/2026-09-06-grep-vs-rag.md、ADR-0003

## 背景

reference 层官方金标 recall@5=0.120 的根因已实证为**发现层索引覆盖缺口**(官方金标 76 篇抽样:按 ID 读 10/10 可达,按标题搜 0/10 命中——索引不收录,与检索算法无关)。同时 PwC 论文《Is Grep All You Need?》给出 grep 在 inline agent harness 下全胜的结论,Anthropic(Claude Code)同路线。用户四轮 grill 拍板:**grep 优先,向量库降级待墙**。

## 决策

1. **建 corpus 语料目录**(`~/.lingeebuild/corpus/`,一文档一 md + front-matter:id/type/url/title/updatedAt/discovered_by):read/ask 深读**同步写穿**全文;发现层(时间网格/官方分享引用)写 stub(标题+摘要);重复发现按 updatedAt 比对,变了才覆盖。**agent 用 rg 直接检索**,替代 FTS5 的检索角色。
2. **sqlite 缩编为纯上游缓存**:search_cache/detail_cache 保留现状(透明、零维护、30 倍暖读);FTS5/chunks 写入与 `local=1` 端点**冻结开发**(manifest 标 deprecated),v6 视 corpus 成熟度删除;向量 BLOB 列冻结待墙。
3. **发现层双轨**:时间网格 `scripts/discovery_sweep.py`(词表×sortsType=2,手动触发,1 请求/秒,单轮 ≤200 请求)为主力;图游走(recommendArray)休眠——**W2 探测定案:recommendArray 匿名不可达**(knowledge/article JSON 均无该字段、详情页有「金蝶云通行证」登录门、question.suggests 恒 0;文档 6 §2.6 的可解析结论产生于登录态 CDP 上下文)。服务保留 recommendArray「出现即提取落盘」逻辑,上游未来匿名放出时零成本启用。
4. **agent 改写做厚**(SKILL.md):口语→术语改写清单 + 多路关键词(2-3 组)+ 先 rg corpus 后 kd search + 引用原链接。语义鸿沟由 LLM 弥补(harness effect),零基础设施。
5. **更新机制定案:同步写穿 + 按需拉取**。不变量:「上游被请求,语料才更新」;无后台线程/无定时任务/无预取。corpus 增长不设限(全量理论 20 万篇/几百 MB,rg 无压力;删目录即重置)。

## 理由

- 我们满足 grep 全胜区的两个条件:检索发生在 inline agent harness(agent 调 rg/kd、读结果、迭代换词);语料为 ERP 术语密集型(报错文案/字段名/版本号 = literal witnesses);
- 非代码语料需自证(论文 Limitations)→ 验收即自证:corpus 建成后跑 usage 层前后对照 + 关上游 rg 冒烟;
- 「提问之后不会回头问相同的问题」(用户)→ 查询缓存日常价值低,但**网络去重**角色(不重复打上游)与语料容器价值不依赖重复提问 → 缩编而非删除;
- 时间网格 1 请求/秒、单轮 ≤200、手动触发,守住人类频率红线;纯追加写,失败重跑即可。

## 后果

- 检索入口变化:agent 第 0 步 = `rg` 本地 corpus(零上游),在线链路(kd ask/search)退居其后;
- 服务新增:POST /corpus 摄入端点(发现层统一入口,零上游)、/health 带 corpus 计数、版本 v5.0;
- corpus 写穿与 sqlite 缓存开关无关(常开,本地毫秒级,不阻塞回答);
- 发现层当前仅时间网格+share 引用两条腿,图游走待上游放出 recommendArray 或将来单独立项(登录态不可用——零账号红线);
- usage 沉淀:每次真实解题会话收尾写 `<corpus>/usage/`,同时是评测集生长源与发现层种子。

## 备选与取舍

- 向量库(sqlite-vec/本地 bge):转述型长尾的对口药,但当前 usage 层(0.756)未撞墙,嵌入通道随向量降级一并搁置;重启条件=「多轮改写仍搜不到」的转述型案例积累出评测数据;
- 全量时间网格常驻定时:违背「不做常驻定时」定案与简单性,弃;
- 登录态拉 recommendArray:触碰零账号红线,弃。

## 增补(2026-09-06 深夜,v5.1:图游走剔除 + corpus 全量快照)

grill 后续三轮(用户拍板)与一次红线豁免,记录如下:

1. **图游走彻底剔除**:recommendArray 提取逻辑、recommendations 字段与 stub 写入从服务中删除(原休眠代码的唯一触发条件=登录态,触零账号红线,永远不执行)。发现层收敛为四条腿:全量快照 / 时间网格 / share 引用 / usage 沉淀。discovered_by 枚举不再含 graph。
2. **corpus 定位升级:全量快照**(用户:「越用越厚不行,第一次检索就要全面」):一次性枚举搜索索引内 93(星空旗舰版=AI 星空)+87(苍穹)全部文档的标题+摘要 stub(实测宽词「金蝶」12,513 条为两产品线之和;单查询硬顶 50 页,global=true 时 productIds 过滤被上游忽略——必须 global=false + productIds)。全文仍按需写穿,不变量不破。
3. **红线豁免(一次性)**:全量快照 1 请求/秒、上限 7,500 请求,用户 2026-09-06 拍板豁免;下次全量刷新需重新拍板,日常增量仍 ≤200 请求/轮。
4. **发版说明通道探明**:schoolapi/search 匿名可用(6,637 条命中)——发版说明与课程同在学习成长中心(school)子系统,立案交接文档 18;帮助中心 DokuWiki 通道单列 issue #2。
