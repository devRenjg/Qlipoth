# multi-turn-context Specification

## Purpose
TBD - created by archiving change multi-turn-context. Update Purpose after archive.
## Requirements
### Requirement: 会话内多轮上下文

系统 SHALL 引入会话（conversation）概念，将同一会话内的连续问答归属在一起。发起带会话标识的查询时，系统 SHALL 携带该会话近几轮问答作为上下文。

#### Scenario: 同一会话的连续提问

- **WHEN** 用户在同一会话内连续提问
- **THEN** 系统将这些问答归属同一会话，后续查询可访问到前几轮问答

#### Scenario: 新会话不串上下文

- **WHEN** 用户开启新会话提问
- **THEN** 系统不携带其它会话的历史，按独立问答处理

### Requirement: 基于上文的指代消解

系统 SHALL 在生成搜索策略前结合会话上文，将含指代/省略的追问补全为完整检索意图，再据此生成关键词。

#### Scenario: 追问消解指代

- **WHEN** 上一轮在问「春晚的研发保障人数」，本轮追问「那市场侧呢」
- **THEN** 系统结合上文将本轮意图补全为「春晚市场侧保障人数」并据此生成搜索关键词

### Requirement: 回答注入对话历史

系统 SHALL 在回答生成时注入近几轮对话，使回答与前文连贯，且仍遵循既有人设与「先总体后明细」要求。

#### Scenario: 连贯的追问回答

- **WHEN** 用户基于上一轮回答做细化追问
- **THEN** 回答延续前文语境作答，不要求用户重述背景

### Requirement: 上下文长度控制

系统 SHALL 对注入的对话上下文做长度上限控制；当历史超出预算时 SHALL 截断或摘要较早轮次，保证总上下文不超出限制且优先保留近轮。

#### Scenario: 历史超出预算

- **WHEN** 会话历史轮次过多、超出上下文预算
- **THEN** 系统按策略截断/摘要较早轮次，保留最近轮次，确保请求不超长

