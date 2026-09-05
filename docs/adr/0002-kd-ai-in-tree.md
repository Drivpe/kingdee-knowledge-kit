# kd ai 入仓:合成能力回到 CLI,带自动降级

v1.0 以「零 LLM 依赖」为由剥离了 kd ai,但第一用户(agent 与个人终端场景)因此断裂:资料包没人帮读。v2.0 决定 kd ai 重新入仓:KAI_BASE/KAI_MODEL 指向任意 OpenAI 兼容端点(接用户自己的渠道,零官方点数),两段 LLM 调用(关键词转写→带引用合成,合成遵循 ANSWER-SPEC);通道不可用自动降级为返回资料包并带 `fallback:true`,行为退化为 v1.0,不产生硬失败。

## Considered Options

- 维持 v1.0 零 LLM:定位最纯,但终端个人体验断裂,「卖给 agent 的 CLI」缺一只手;
- 服务端做 /ai:把模型依赖引进纯标准库服务,违背「服务只做检索内核」的分层纪律。

## Consequences

- 卖点从「零 LLM 依赖」改写为「零官方 LLM/零点数,模型通道接你自己的」;
- 模型通道是可选依赖:没配 KAI_BASE 时等价 v1.0,kd ai 只会降级。
