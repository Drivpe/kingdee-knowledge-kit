# 票 #17:--kw 显式关键词路丢失 --product 过滤(硬 bug)

状态:✅ 完成(2026-09-06,修复+重启+验证) | 依赖:无 | 优先级:P0

## 问题
`plan_routes` 的 keywords 显式分支(service/kingdee-ksearch-service.py:781-788)在 productIds 统一注入(:852-855)之前提前 return,导致 `kd ask --kw "..." --product 93` 的所有路不带 productIds,企业版内容串线进旗舰版问题(2026-09-06 BOM 表单标识事故的第二条串线通道)。

## 改动
keywords 分支内补同权产品过滤后再 return(与主路径逻辑一致)。仓库与部署副本(~/.lingeebuild/config/)同步修改。

## 附带发现
部署副本落后于仓库(仍是 v6.0 时代文案:corpus 检索面/发现层语义未更新),已用仓库版整体覆盖同步。**部署同步流程有缺口**——建议后续确认部署副本的生成方式(拷贝脚本?手动?),避免"改了仓库没生效"这类事故。

## 验收
`kd ask "BOM维护" --kw "BOM维护" --product 93 --topk 1` → routes=[{kind:"explicit",terms:"BOM维护",productIds:93}] ✅;上游预算 0/16(缓存命中)✅;服务重启后 health v6.1 正常 ✅。
