# openspec-dashboard Specification

## Purpose
TBD - created by archiving change openspec-dashboard. Update Purpose after archive.
## Requirements
### Requirement: 管理员可见的 OpenSpec 看板入口

系统 SHALL 在主导航提供「OpenSpec」入口，且仅对管理员（admin）角色可见。非管理员角色 SHALL 既看不到该入口，也无法通过直接访问其路由/接口获取数据。

#### Scenario: 管理员看到入口

- **WHEN** 管理员登录并查看主导航
- **THEN** 导航中出现「OpenSpec」Tab，点击可进入看板页

#### Scenario: 非管理员不可见且被拦截

- **WHEN** 普通或 super 角色用户查看主导航或直接访问 OpenSpec 接口/路由
- **THEN** 导航中不出现该入口，且接口返回无权限（403）

### Requirement: 展示已实现能力明细

系统 SHALL 实时读取 `openspec/specs/` 下的能力规格，展示每个已实现能力的名称、用途（Purpose）及其 Requirement/Scenario 明细。

#### Scenario: 列出已实现能力

- **WHEN** 管理员进入 OpenSpec 看板
- **THEN** 系统列出 `openspec/specs/` 下所有能力，每项含能力名与需求条数

#### Scenario: 查看单个能力明细

- **WHEN** 管理员展开某个已实现能力
- **THEN** 系统展示该能力的 Purpose 与各 Requirement 及其 Scenario 内容

### Requirement: 展示待实现提案列表

系统 SHALL 实时读取 `openspec/changes/`（不含 archive）下的变更提案，展示每个待实现 Feature 的名称、改动概要（proposal）与任务完成进度。

#### Scenario: 列出待实现提案

- **WHEN** 管理员进入 OpenSpec 看板
- **THEN** 系统列出 `openspec/changes/` 下所有未归档提案，每项含名称与任务进度（已完成/总数）

#### Scenario: 查看单个提案详情

- **WHEN** 管理员展开某个待实现提案
- **THEN** 系统展示该提案的 What Changes 概要与任务清单

### Requirement: 数据实时来源于 OpenSpec 文件

系统 SHALL 在请求时实时读取 `openspec/` 目录文件作为数据源，不维护独立副本；当 specs/changes 文件变更后，看板内容 SHALL 随之更新而无需改代码。

#### Scenario: 文件变更后看板同步

- **WHEN** `openspec/specs/` 或 `openspec/changes/` 下文件发生增删改后，管理员刷新看板
- **THEN** 看板展示的能力/提案与当前文件内容一致

