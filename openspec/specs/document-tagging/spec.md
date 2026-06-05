# document-tagging Specification

## Purpose
TBD - created by archiving change document-tagging. Update Purpose after archive.
## Requirements
### Requirement: 文档标签维护

系统 SHALL 支持为每篇文档维护零个或多个标签，标签可新增、删除、重命名。同一文档的标签集合 SHALL 去重。

#### Scenario: 给文档打标签

- **WHEN** 用户为某文档添加一个或多个标签
- **THEN** 系统持久化文档与标签的关联，重复添加同名标签不产生重复关联

#### Scenario: 删除标签

- **WHEN** 用户删除某文档上的一个标签
- **THEN** 系统移除该文档与标签的关联，不影响其它文档对该标签的使用

### Requirement: 按标签筛选文档

系统 SHALL 在文档管理页支持按一个或多个标签筛选文档列表，并展示每篇文档的标签。

#### Scenario: 单标签筛选

- **WHEN** 用户在文档管理页选择某个标签筛选
- **THEN** 列表仅展示带该标签的文档

#### Scenario: 多标签筛选

- **WHEN** 用户同时选择多个标签筛选
- **THEN** 列表按约定（并集）返回具备所选标签中任一个的文档

### Requirement: 上传/导入时指定与建议标签

系统 SHALL 允许在上传或链接导入时指定标签，并 SHALL 基于文档元数据（来源、@负责人、年份等）给出建议标签供用户确认。

#### Scenario: 导入时带标签

- **WHEN** 用户在上传/导入时指定了标签
- **THEN** 文档落库后即带上这些标签

#### Scenario: 自动建议标签

- **WHEN** 导入的文档含可识别的元数据（如来源域名、@负责人、年份）
- **THEN** 系统给出建议标签，用户可一键采纳或忽略

### Requirement: 搜索按标签过滤

系统 SHALL 支持在查询时可选地按标签缩小候选文件范围，与既有文件名模式（file_pattern）协同生效；未指定标签时行为与现状一致。

#### Scenario: 带标签的查询

- **WHEN** 用户发起查询并指定了标签过滤
- **THEN** 搜索仅在带该标签的文档范围内进行排序与摘录

#### Scenario: 不带标签的查询保持现状

- **WHEN** 用户发起查询且未指定任何标签
- **THEN** 搜索在全库范围进行，行为与未引入标签前一致

