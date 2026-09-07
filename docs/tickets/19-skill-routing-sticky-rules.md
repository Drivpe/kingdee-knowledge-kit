# 票 #19:SKILL/ANSWER-SPEC 写入产品路由粘性与引用核对硬规则

状态:✅ 完成(2026-09-07) | 依赖:#18(✅,透传后才有字段可核对) | 优先级:P1

## 改动(原案)
①同一问题会话内 --product 路由必须粘性,后续 search/read 禁止变道 0;②引用前必须核对每源的 products 字段,产品线不符的不引不答。写入 skills SKILL.md 与 docs/ANSWER-SPEC.md。

## 实际改动(纯文档,零代码;未碰 service/、cli/)

### ① skills/kingdee-knowledge/skills/kingdee-knowledge/SKILL.md(仓库内打包副本;用户安装副本 ~/.zcode/skills/ 未动,属另一独立决策)

1. **新增小节「产品路由粘性与引用前核对(硬规则,2026-09-06 旗舰版串线事故条款)」**
   (插在「手动细粒度调试命令(search/read)」与「回答规范」之间,对两者同时生效):
   - 规则 1 产品路由粘性:首次 ask 确定的 product 路由(如 `--product 93`)**必须**贯穿会话内
     后续所有 search/read/ai/ask 调用;**禁止**中途变道 `--product 0`、换产品线过滤或不带
     --product 重新检索;追问出现新产品线线索**必须先向用户确认**再切换,禁止自行/静默切换;
   - 规则 2 引用前核对:引用任何 source 前**必须**核对其 `products` 字段与顶层
     `effectiveProductId` 一致(或包含目标产品线);**禁止**把产品线不符的源作为答案依据
     (不论相关性/排序,症状对齐引用也不行);`effectiveProductId` 与预期不符**必须停止
     检索并报告用户,禁止**带错误过滤继续作答。
   - 小节开头保留事故一句话(93→0 变道、企业版 ENG_BOM/T_ENG_BOM 当旗舰版输出)作锚。
2. **ask 返回体描述同步(票 #18 字段)**:`sources[]` 增加 `products`、顶层增加
   `effectiveProductId`,规则 2 的核对有数据可依。
3. **手动命令节**:search 示例的 `--product` 注释下加一行 ⚠️ 粘性提示(沿用会话首次 ask 路由,禁止变道 0)。
4. **禁止事项**新增两条,与硬规则一一对应(变道 0/换产品线;引用不符产品线的源、
   effectiveProductId 不符必须停下报告)。

### ② docs/ANSWER-SPEC.md(v2.1 → v2.2)

1. **规范新增第 7 条「产品线甄别(硬规则,v6,票 #19)」**,与第 3 条下的
   ADR-0007 引用标准形态小节并列;第 3-6 条原文未动:
   - 产品路由粘性(必须贯穿 / 禁止变道 0 / 新产品线线索必须先确认);
   - 引用前核对(`products` vs `effectiveProductId`,不符禁止作答案依据);
   - 过滤回显不符即停(effectiveProductId 与预期不符→停止并报告,禁止继续作答)。
2. **反例(禁止)新增一条**:引用 products 不符的源(点名旗舰版问题引企业版
   ENG_BOM/T_ENG_BOM 的 2026-09-06 事故反例)、中途把已确定 --product 变道为 0。
3. 引用规范既有小节(第 3 条 ADR-0007 部分、第 6 条根因引用规则)逐字保留,未破坏。

### ③ 同步说明

ANSWER-SPEC 声明「三处引用它、不各写一份」(kd ai 提示词/技能文档/README),本票只改
SPEC 与技能文档两处规范源头;kd ai 的内置提示词属 cli/ 代码,按纪律本票不动,
其引用关系保证 SPEC 更新即生效面(此点与票 #20 的 cli 同批改动模式一致)。

## 验收②:金标用例纸面演练(use-bom-designer-page-2026-09-06)

用例:data/eval/evalset.json,`product: 93`,问题「BOM维护在开发平台的那个页面 金蝶旗舰版」。
事故当天轨迹:① ask(93)起步 → ② 中途变道 `kd search "ENG_BOM" --product 0` →
③ 召回企业版内容(ENG_BOM/T_ENG_BOM,products=["企业版/标准版"])未甄别 →
④ 把企业版答案当旗舰版输出(正确:旗舰版 pdm_mftbom)。

逐条对照新规则,事故每一步在哪一条规则处被拦下:

| 事故步骤 | 被哪条规则拦下 | 拦下后的正确动作 |
|---|---|---|
| ② 中途变道 `--product 0` | **规则 1(路由粘性)**:会话内首次 ask 已定 93,后续所有 search/read **必须**沿用,**禁止**变道 0——此步在发起前即被禁止 | 维持 `--product 93` 检索;若确需 ENG_BOM 线索,先向用户确认「您问的是星空企业版的 ENG_BOM 吗」,经确认才能切路由 |
| ③ 企业版源进入候选(products=["企业版/标准版"]) | 即便过滤已被绕过(如 --kw 类 bug),**规则 2(引用前核对)**:该源 products 与 effectiveProductId=93 不符,**禁止**作为答案依据——症状对齐引用也不行 | 弃用该源;只引用 products 含旗舰版/与 93 一致的源 |
| ④ 把企业版内容当旗舰版输出 | 规则 2 的兜底:逐源核对后,企业版源全部出局,输出只剩旗舰版依据;无合规依据时按 ANSWER-SPEC 第 4 条诚实声明「现有资料未覆盖」 | 引用旗舰版正确文档(pdm_mftbom 所在的 knowledge 集),或诚实声明未覆盖 |
| (前置防线)资料包顶层 effectiveProductId 回显异常(如要 93 却回 0,过滤被静默丢弃) | **规则 2 的「过滤回显不符即停」**:必须停止检索并报告用户,**禁止**带错误过滤继续作答 | 停下报告「本次产品过滤未生效」,修调用方式后重试 |

**演练结论**:四步事故链条在新规则下有三道独立闸门——变道前(规则 1)→ 召回后引用前
(规则 2 products 核对)→ 输出前(effectiveProductId 不符即停 + products 全核对)。
任一闸门生效都不会输出「企业版 ENG_BOM 当旗舰版」的答案;且依赖的字段(sources[].products、
effectiveProductId)已由票 #18 上线,规则可执行。

## 验收③:证据

- 改动文件(绝对路径):
  - D:\01_Work\03_Develop\Lingya\kingdee-knowledge-kit\skills\kingdee-knowledge\skills\kingdee-knowledge\SKILL.md
  - D:\01_Work\03_Develop\Lingya\kingdee-knowledge-kit\docs\ANSWER-SPEC.md
  - 本票文件
- 未触碰:C:\Users\Drivpe\.zcode\skills\ 下用户安装副本(独立决策)、service/、cli/、测试;
  无联网、无 git 提交。

## 遗留

- 用户安装副本 ~/.zcode/skills/kingdee-knowledge/SKILL.md 与仓库版存在分化(仓库版已带
  chunk 溯源/本票硬规则),是否用仓库版覆盖安装副本待另拍板(独立决策,不随本票)。
