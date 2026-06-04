# chat-history Specification

## Purpose
持久化每次问答（问题、回答、引用来源、时间、用户），在聊天页左侧栏按时间倒序展示并支持回显，并按角色控制可见范围：管理员/超级用户可见全员历史，普通用户仅见自己的历史。

## Requirements

### Requirement: 问答历史持久化

系统 SHALL 在每次问答后保存一条历史记录，含问题、回答、引用来源（source_urls）、创建时间（北京时间）与所属用户。

#### Scenario: 保存一次问答

- **WHEN** 一次问答完成
- **THEN** 系统向聊天历史表插入一条含 question/answer/source_urls/created_at/user_id 的记录

### Requirement: 历史列表与回显

系统 SHALL 提供按时间倒序的历史列表（支持数量上限），列表项可回显对应问答的问题、回答与引用来源。

#### Scenario: 按时间倒序展示

- **WHEN** 用户打开聊天页
- **THEN** 左侧栏以创建时间倒序展示历史列表，点击某项可回显该条问答的完整内容与引用

### Requirement: 按角色控制可见范围

系统 SHALL 按角色控制历史可见范围：admin 与 super 可查看所有用户的历史，user 仅可查看自己的历史。

#### Scenario: 普通用户只看自己

- **WHEN** user 角色请求历史
- **THEN** 系统按其 user_id 过滤，仅返回该用户自己的历史记录

#### Scenario: 管理员查看全员历史

- **WHEN** admin 或 super 请求历史（不带 user 过滤）
- **THEN** 系统返回所有用户的历史，并关联展示各记录所属用户名

### Requirement: 删除历史记录

系统 SHALL 支持按记录 id 删除单条历史。

#### Scenario: 删除一条历史

- **WHEN** 用户对某条历史发起删除
- **THEN** 系统按 id 从聊天历史表移除该记录
