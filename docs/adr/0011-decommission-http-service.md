# ADR-0011:去服务化 —— 检索内核改为库 + 纯 CLI

日期:2026-09-16 | 状态:已采纳 | 关联:ADR-0005(v6 彻底在线)、ADR-0008(合成权移交)、
docs/research/2026-09-16-服务化与纯CLI架构调研.md、docs/research/2026-09-16-CLI工具发行方式调研.md

## 背景

本套件当前形态:一个长驻本地 HTTP 服务(`http.server`,默认 `127.0.0.1:4097`,1351 行单文件)
承载全部检索逻辑,外加一个薄 CLI(`cli/kd.py`,249 行)经 HTTP 调用它;安装器用
`nohup ... &`(Linux)/ `Start-Process -WindowStyle Hidden`(Windows)把服务静默拉起为后台进程。

这个形态是 v4/v5 时期的产物,当时预期"多个客户端共享一个服务"。**v6 之后这个前提不再成立**
(ADR-0005 彻底在线、ADR-0008 合成权移交后,唯一调用方就是 agent 手里的 `kd`),但服务层从未被撤掉。

两轮一手资料调研(见关联文档)给出的核心事实:

| 事实 | 来源 |
|---|---|
| `"检索工具必须拉起服务"不是 Python 生态约束` | 本项目自查 + 两轮调研 |
| console_scripts / `[project.scripts]` 规范语义**不含任何后台进程** | PyPA entry-points 规范 |
| SQLite 官方明文 **"doesn't require a separate server process"**;WAL 下读写互不阻塞,写者唯一由**文件锁**在引擎层保证 | sqlite.org/wal.html、Python sqlite3 文档 |
| 业界服务化的正当理由是**可量化的**(摊销进程启动开销),不是"更稳" | `blackd` 官方:"avoid the cost of starting up a new Black process every time" |
| 15 个主流 CLI 样本中带服务的 4 个(`blackd`/`jupyter lab`/`streamlit run`/`label-studio`)**无一在安装阶段自动拉起服务** | CLI 发行方式调研 §一 |
| 15/15 样本都发布到 PyPI;12/15 有 `pyproject.toml`;**没有任何一个是"无元数据 + 拷文件 + 手写 shim"** | 同上 |
| 本项目服务建在 `http.server` 上,官方警告 **"not recommended for production"** | Python 官方文档 |
| "有 pyproject.toml 之后仍保留一键脚本"是**主流组合**(poetry/uv/ruff/awscli 四例) | 同上 |

**决定性判据**:服务化唯一站得住的技术理由是"避免每次重新加载昂贵资源"。本项目里那是
`semantic_rerank` 的 96MB ONNX 模型。但实测:该模块**默认关**(`KSEARCH_RERANK_SEMANTIC=off`)、
**模型从未下载**、`tokenizers` 未安装、评测未过门——**这个理由从未被激活**。
而实测代价:CLI 冷启动 0.09–0.15 秒,服务常驻占 37.4MB 内存。

## 决策

### 1. 检索内核改为可 import 的库,HTTP 层删除

`service/kingdee-ksearch-service.py` 拆为包 `src/kd/`,业务逻辑与 HTTP 解耦:

```
src/kd/
  core.py      # 检索编排:plan_routes / knowledge_search / _rrf_fuse / _select_top /
               # ask_bundle / chunk_text / top_chunks / Budget / RateLimiter / clamp_query
  docstore.py  # 落盘(已是独立模块,直接搬)
  cli.py       # argparse 入口(原 cli/kd.py 的命令面)
  __main__.py  # python -m kd
```

依据:`cli/kd.py` 与服务之间**只有 HTTP、零 import**,且服务本身就是 `docstore` + `semantic_rerank`
加一层 HTTP 包装——**架构上不存在阻碍**。旁证:`releasenotes_ingest.py` 早已直接 `import docstore` 不走服务;
`kd read --chunk` 也已在 CLI 内直连上游(其源码注释亦写着"应收编进检索服务")。

**十个 HTTP 端点(`/search` `/ask` `/health` `/corpus` `/karticle` `/question` `/answer`
`/article` `/share` `/manifest`)与 `docs/API.md` 一并删除**,只留 CLI。

### 2. 不留兼容层

服务代码直接删除,不做"双路径并存"。理由:唯一调用方是 `kd` 自身,保留 HTTP 层等于继续背着
端口冲突、进程生命周期、日志丢弃这三类已观测到的运维代价,却无人受益。

### 3. 取消本地落盘缓存

`corpus/`、`~/.lingeebuild/landing`、`data/ksearch.db` 三处缓存全部取消。
理由:重复问同一件事的概率低,维护成本高于收益。

**连带后果(已确认接受)**:`SKILL.md` 第 2 步(对 `landing` 做 `rg` 字段名级精查)**整节作废**,
v6 的"三级回退"退化为两级;`kd health` 的 `landing` 字段、`docstore` 的落地缓存用途一并清理。

### 4. 删除 `semantic_rerank`

删模块与依赖。理由:它要解决的问题(关键词搜不到但语义相关)**已由 ADR-0009 的原句路解决**——
后者零依赖、已上线、有实测背书(原句排第 1 vs 片段融合第 23)。在修好的问题上再修一遍,
且需 96MB 模型 + 两个额外依赖。

**注意**:这不是否定票 #21 的调研价值,而是判定其**实现路径**已被更廉价的方案取代。

### 5. 数据目录与上游纪律

限速器与预算计数器从"进程内共享"改为**模块级 + 跨进程共享状态**(SQLite 存上次请求时间)。
理由:采集类脚本(`run_eval.py` 循环用例池)是**自动循环**而非人工逐次触发,去掉服务的共享
限速器后可能越界,触碰"保持人类调用频率"红线。

**⚠️ SQLite 必须补 WAL 与 busy_timeout**:现实现 `sqlite3.connect(DB_PATH, check_same_thread=False)`
既未设 `journal_mode=WAL` 也未显式设 `busy_timeout`。单进程独占时未暴露;多进程(每次 `kd` 调用
一个进程)后**必须**补上,否则会踩 `SQLITE_BUSY`。此为迁移前置条件,不是可选项。

### 6. 命名:保留 `kd`,不改

否决"命令统一 `ly` 前缀":`~/.kd/` 目录**已被 `ly` 占用**(`ly/config.py:13` 读 `~/.kd/config.json`,
`ly/session.py` 明确"web 密码只存 `~/.kd/config.json`")——改名会造成**配置目录碰撞**,且撞的是存凭据处。
`ly` 自身已有 20 个子命令,`ly` 亦为 Lingya 专名。

**替代方案**:`ly` 侧新增转发入口,用 `shutil.which("kd")` 定位并转发子进程,**6 个命令全部转发**,
保持 `kd` 的 stdout=JSON / stderr=进度 / 退出码契约。两包独立发布,不互相依赖。

### 7. 打包与安装

- 建 `pyproject.toml`,`[project.scripts] kd = "kd.cli:main"`,发布到 PyPI;
- **保留一键脚本**(删掉服务拉起那段):数据表明"pyproject + 一键脚本"是主流组合;
- **同时支持 `pipx install`**:pipx 是 PyPI 官方为"安装命令行应用"场景给出指引的方案
  (注意其措辞是 "consider",PyPA 明示不做全局唯一推荐);
- 技能部署对齐 `ly`:canonical 目录 + 软链(失败回退拷贝),覆盖 `workbuddy`/`zcode`/`opencode`/`pi`/`agents`;
- 本机 `kd` 与技能**从未真正安装过**(`~/.kingdee-kit`、`~/.agents/skills` 均为空),一直在仓库内运行——
  这说明当前手写安装器本身就有门槛。

### 8. 实施顺序

**先建新路径并验证,再删旧代码**——避免一次性跨两仓大改导致问题无法定位。
验收以**手工用例**为主(BOM 金标、长问句 100 字硬闸逐条验),不依赖 `run_eval`
(评测体系已因精度不足搁置)。

## 后果

**收益**
- 安装不再需要后台进程、端口、生命周期管理;`pipx install kd` 即得命令;
- 删除三类已观测的运维代价:固定端口冲突、崩溃无日志(`>/dev/null`)、升级需 `fuser -k` 杀旧进程;
- 代码去一层 HTTP 编解码,内核可被直接 import 与单元测试;
- 与 15 个主流 CLI 样本的发行方式对齐(此前是孤例)。

**代价**
- **失去多客户端共享**:未来若有其他进程(非 kd 调用方)想走 HTTP 取数,需重新服务化;
- **失去落地缓存**:`rg` 精查能力与"查过即毫秒复用"消失,每次重新请求上游;
- **`semantic_rerank` 路线关闭**:若将来重新启用语义重排,**冷启动重载模型会反向要求服务化**——
  届时须重新评估本 ADR;
- 跨两仓同步改动,且公开仓的 `verify_ksearch.py` 大量断言针对 HTTP 端点,需重写为纯 CLI 测试。

## 备选与取舍

- **保留服务但默认不拉起**(照 `blackd` 模式:服务走 optional extra、用户手动启动):
  被否——本项目没有"用户想自己跑服务"的场景,留一个没人用的服务端只是负债;
- **同包双入口 `kd` + `kd-serve`**:被否,理由同上;`black`/`blackd` 成立是因为真有工具库
  (编辑器插件)需要长期 HTTP 端点,本项目无此需求;
- **保留 `semantic_rerank` 并在启用时再服务化**:被否——先删代码,结论写进本 ADR 即可,
  留着未启用的模块是"永不亮的路";
- **改名统一 `ly` 前缀**:被否,理由见决策 6(配置目录与凭据路径冲突);
- **只修 WAL/日志等局部缺陷、不动架构**:被否——那些缺陷的**根因**就是服务化本身,
  不拆服务只会持续产生同类问题。

## 未决与待办

- `~/.lingeebuild/releasenotes` **实际为空(0 篇)**,而 `SKILL.md` 将其写为「官方已修复类问题的
  终审依据」、第 2 步教用 `rg` 查它。文档承诺了不存在的能力——单独记为待办,不在本 ADR 范围。
