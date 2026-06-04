# document-ingestion Specification

## Purpose
将用户上传的多格式文档解析为统一的 Markdown 纯文本落盘到知识库，并在解析过程中提取负责人元数据、做同名去重与导入历史记录，保证知识库内容可读、可搜索、可追溯。

## Requirements

### Requirement: 多格式文档解析为 Markdown

系统 SHALL 支持 `.docx`、`.xlsx`、`.xls`、`.pptx`、`.pdf`、`.md`、`.txt` 七种格式的上传，并将其解析为 Markdown 纯文本存储到知识库目录。对于不支持的扩展名，系统 SHALL 拒绝上传并返回错误，提示当前支持的格式。

#### Scenario: 上传 Word 文档

- **WHEN** 用户上传一个 `.docx` 文件
- **THEN** 系统提取段落文本、标题层级与表格内容，转为 Markdown（标题用 `#` 层级、表格用 `| |` 管道语法）并落盘为同名 `.md` 文件

#### Scenario: 上传 Excel 工作簿

- **WHEN** 用户上传一个 `.xlsx`/`.xls` 文件
- **THEN** 系统按 Sheet 分别提取，每个 Sheet 转为一个 Markdown 表格，宽表（空列分隔的多区块）自动拆分为多个表格

#### Scenario: 上传不支持的格式

- **WHEN** 用户上传一个扩展名不在支持列表内的文件
- **THEN** 系统拒绝该上传，返回错误信息并列出当前支持的格式

### Requirement: 图片型 PDF 自动 OCR

系统 SHALL 优先提取 PDF 的文本层；当 PDF 无可提取文本（图片型）时，系统 SHALL 将每页渲染为图像后经 Tesseract OCR（中英文）提取文字。

#### Scenario: 文本型 PDF

- **WHEN** 用户上传一个含文本层的 PDF
- **THEN** 系统直接提取各页文本，按「第 N 页」分节存储，不触发 OCR

#### Scenario: 图片型 PDF

- **WHEN** 用户上传一个无文本层（扫描/海报型）的 PDF
- **THEN** 系统将每页渲染为图像并用 Tesseract（`chi_sim+eng`）OCR，超大图按高度切片后拼接识别结果

### Requirement: 负责人元数据提取

系统 SHALL 从文档文本中提取形如 `@人名` 的提及作为负责人（Owner），去重后写入文档头部的负责人元数据行。

#### Scenario: 文档含 @人名

- **WHEN** 上传的文档正文包含一个或多个 `@人名` 提及
- **THEN** 系统去重提取这些人名，在落盘 Markdown 顶部插入 `> 负责人: @张三, @李四` 形式的元数据行

### Requirement: 同名文档去重

系统 SHALL 在上传前按原始文件名检查是否已导入；若已存在同名记录，系统 SHALL 拒绝重复导入。当落盘文件名冲突时，系统 SHALL 以数字后缀生成不冲突的存储名。

#### Scenario: 重复上传同名文件

- **WHEN** 用户上传一个 `original_name` 已存在于文档记录中的文件
- **THEN** 系统拒绝导入并提示「文件已导入过，无需重复导入」

#### Scenario: 落盘文件名冲突

- **WHEN** 目标存储名 `X.md` 已存在于知识库目录
- **THEN** 系统改用 `X_1.md`、`X_2.md` 等带数字后缀的名称落盘，避免覆盖既有文件

### Requirement: 导入历史记录

系统 SHALL 在文档成功落盘后记录元数据（原始名、存储名、类型、大小、北京时间戳）到文档表，并写入一条导入历史（导入树）记录。

#### Scenario: 上传成功后记录元数据

- **WHEN** 一个文件成功解析并落盘
- **THEN** 系统向文档表插入一条含原始名/存储名/类型/大小/上传时间的记录，并写入对应的导入树记录
