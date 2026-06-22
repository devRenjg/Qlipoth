# llm-integration Specification

## Purpose
统一封装对多家大模型的调用，兼容 OpenAI Chat Completions 与 Anthropic Messages 两种 API 格式，支持流式输出、失败重试，并注入「活动保障知识库助手」人设以保证回答的全局视角与一致口吻。

## Requirements

### Requirement: 双格式 API 兼容

系统 SHALL 同时兼容 OpenAI Chat Completions 与 Anthropic Messages 两种 API 格式，按配置的 `llm_api_format` 选择对应的请求构造、鉴权头与响应解析；切换模型 SHALL NOT 需要改动调用方代码。

#### Scenario: 调用 OpenAI 格式模型

- **WHEN** 配置 `llm_api_format` 为 openai（如 DeepSeek/通义/智谱/Moonshot/OpenAI）
- **THEN** 系统以 `/v1/chat/completions` 端点、`Authorization: Bearer` 头发起请求，并从 `choices[].message.content` 解析回答

#### Scenario: 调用 Anthropic 格式模型

- **WHEN** 配置 `llm_api_format` 为 anthropic
- **THEN** 系统以 `/v1/messages` 端点、`anthropic-version` 头发起请求，将 system 消息单列到 `system` 字段，并从 `content[].text` 解析回答

### Requirement: 流式输出

系统 SHALL 支持以流式方式逐分片返回回答内容，分别正确解析 OpenAI 的 `delta.content` 与 Anthropic 的 `content_block_delta` 增量格式。

#### Scenario: 流式逐分片产出

- **WHEN** 以流式模式生成回答
- **THEN** 系统按 API 格式解析增量事件，逐个产出非空文本分片，遇到结束标记停止

### Requirement: 失败重试

系统 SHALL 在非流式调用遇到 5xx、空响应或连接/超时错误时按指数退避重试（至多 3 次）；遇到 4xx 客户端错误 SHALL 立即失败并返回错误。

#### Scenario: 服务端 5xx 重试

- **WHEN** LLM 接口返回 5xx 或空响应
- **THEN** 系统按指数退避重试，至多 3 次，全部失败后抛出错误

#### Scenario: 客户端 4xx 不重试

- **WHEN** LLM 接口返回 4xx 客户端错误
- **THEN** 系统不重试，立即抛出含状态码与响应片段的错误

### Requirement: 人设注入与全局视角

系统 SHALL 在生成回答时注入可配置的助手人设（来自本地 `Soul.md`，公开仓库不含真实人设），要求直接作答、先给整体/总计再给分类明细、不暴露检索过程。

#### Scenario: 数量类回答先总后分

- **WHEN** 用户问涉及数量/人数的问题
- **THEN** 回答先给出整体/总计数据，再按需给出分类明细，且不暴露搜索或检索过程
