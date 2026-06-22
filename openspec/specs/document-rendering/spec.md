# document-rendering Specification

## Purpose
将知识库文档与 AI 回答中的 Markdown 文本渲染为结构化、排版良好的富文本 HTML，并对不可信内容做 XSS 净化、对外链做安全打开处理。
## Requirements
### Requirement: Markdown 结构化渲染

系统 SHALL 将 Markdown 文本渲染为结构化富文本 HTML，至少支持 GitHub Flavored Markdown 的表格、标题、有序/无序列表、链接、行内代码与代码块、加粗与斜体。

#### Scenario: 渲染表格密集的文档

- **WHEN** 用户在文档管理页点击「查看」一篇含 Markdown 表格的文档
- **THEN** 弹窗以带边框的 HTML 表格呈现该内容，而非显示 `|---|` 等源码字面量

#### Scenario: 渲染标题与列表

- **WHEN** 文档包含 `#`/`##` 标题与 `-`/`1.` 列表
- **THEN** 标题以层级化样式呈现，列表以项目符号/编号呈现

#### Scenario: 聊天回答中的结构化内容

- **WHEN** AI 回答文本包含 Markdown 表格或列表
- **THEN** 聊天回答区以渲染后的富文本展现这些结构，而非源码字面量

### Requirement: 渲染内容安全净化

系统 SHALL 对渲染后的 HTML 进行净化，移除脚本及危险属性，防止上传的不可信文档内容造成 XSS。

#### Scenario: 净化恶意脚本

- **WHEN** 文档内容包含 `<script>` 标签或 `onerror` 等事件属性
- **THEN** 渲染结果中不包含可执行脚本或危险事件属性

### Requirement: 外部链接安全打开

系统 SHALL 将渲染内容中的链接在新标签页打开，并附加 `rel="noopener noreferrer"`。

#### Scenario: 点击文档中的外链

- **WHEN** 用户点击渲染内容中的一个 `http(s)` 链接
- **THEN** 浏览器在新标签页打开该链接，且链接带有 `target="_blank"` 与 `rel="noopener noreferrer"`


### Requirement: 文档新旧版本对比查看

对经企微 API 重导替换过的文档（has_old_version=1），文档管理页 SHALL 提供"查看新"与"查看旧"两个入口，分别渲染重导后的新版（含 kb-image 本地图片）与重导前备份的旧版（knowledge_base_old/）。未重导的文档 SHALL 仅显示常规"查看"。文档列表 SHALL 每约 20 秒静默刷新，使后台重导新增的文档及其新旧入口自动出现。

#### Scenario: 新旧对比

- **WHEN** 用户对一个重导过的文档点击"查看旧"
- **THEN** 打开重导前的原始内容渲染页；点击"查看新"打开重导后的内容，便于人工对比质量

### Requirement: 在线文档图片渲染

文档渲染 SHALL 正确显示图片。企微 API 落地的本地图片经 `/api/documents/kb-image/{name}` 同源服务直接显示；内部 wiki 图片经携带 cookie 的服务端代理 `/api/documents/img-proxy` 显示。

#### Scenario: info 图片经代理显示

- **WHEN** 渲染含内部 wiki 图片的文档
- **THEN** 图片经服务端 cookie 代理加载显示，而非因登录态缺失而失败
