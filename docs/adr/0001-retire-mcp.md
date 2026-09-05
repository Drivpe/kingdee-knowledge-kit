# 剔除 MCP 层,kd CLI 为唯一入口

MCP 适配器曾把检索服务暴露给 ZCode,但它是唯一只覆盖单一客户端的接入方式:技能+Bash 调 CLI 对 agent 达到同等效果,还免掉「改工具要重启客户端」;CLI 同时服务人和所有 agent。因此 v3.1 起归档 MCP 适配器、清空注册,接入面收敛为 kd CLI 一个入口,服务端也不做原生 /mcp(留待真有多客户端工具发现需求再议)。

## Considered Options

- 保留 MCP 与 CLI 双轨:多一个要维护的适配层,收益只有 ZCode 原生工具面板;
- 服务端原生 /mcp:同上,且把协议复杂度引进纯标准库服务。

## Consequences

- agent 接入靠技能文档 + `kd manifest` 自发现,而非工具列表;
- ZCode 重启后 MCP 工具消失是预期行为,由技能接棒。
