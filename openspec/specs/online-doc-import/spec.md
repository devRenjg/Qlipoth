# online-doc-import Specification

## Purpose
通过 Playwright 无头浏览器抓取企业微信文档/腾讯文档链接，解码为 Markdown 落盘到知识库，支持递归导入嵌套子文档、SSE 实时进度推送、按 URL 去重、失败记录持久化与父子关系追溯。

## Requirements

### Requirement: 受支持的在线文档链接抓取

系统 SHALL 仅接受企业微信文档（`doc.weixin.qq.com` 等）与腾讯文档（`docs.qq.com` 等）域名的 `http(s)` 链接，对其它链接 SHALL 拒绝。系统 SHALL 通过 Playwright 拦截文档数据接口（`dop-api/opendoc` / `dop-api/get/sheet`）并解码出标题与正文，保留 HYPERLINK 为 Markdown 链接格式。

#### Scenario: 导入受支持的腾讯文档链接

- **WHEN** 用户提交一个 `docs.qq.com` 或 `doc.weixin.qq.com` 的文档链接
- **THEN** 系统用 Playwright 打开并拦截文档数据接口，解码出标题与正文，转为带 `> 来源: <url>` 头部的 Markdown 落盘

#### Scenario: 拒绝不受支持的链接

- **WHEN** 用户提交一个非企业微信/腾讯文档域名的链接
- **THEN** 系统拒绝导入，返回「仅支持腾讯文档/企业微信文档」错误

#### Scenario: 未登录态需要登录

- **WHEN** 抓取时页面要求登录/扫码且未拦截到数据接口
- **THEN** 系统返回需要登录的提示，引导用户在弹出的浏览器窗口完成登录后重试

### Requirement: 递归抓取嵌套子文档

系统 SHALL 支持用户指定 0-3 层的递归深度（0 表示不递归），并在该深度内沿文档内的子链接抓取嵌套子文档。父文档已导入 SHALL NOT 阻断递归，子文档照常录入。

#### Scenario: 递归层数超出范围

- **WHEN** 用户提交的递归层数小于 0 或大于 3
- **THEN** 系统拒绝请求，提示递归层数范围为 0-3

#### Scenario: 父文档已导入仍递归子文档

- **WHEN** 以递归模式导入，根文档此前已被导入
- **THEN** 系统跳过该已导入的根文档（计入已存在），但继续抓取并录入其尚未导入的子文档

### Requirement: SSE 实时进度推送

系统 SHALL 在递归导入过程中以 Server-Sent Events 逐文档推送进度事件（成功/跳过/失败），并在结束时推送汇总（总数、成功、失败、跳过）。

#### Scenario: 逐文档推送进度

- **WHEN** 递归导入正在进行
- **THEN** 系统对每个处理完的文档推送一条 `progress` 事件（含 status、title、depth、url），全部完成后推送 `done` 事件含成功/失败/跳过计数

### Requirement: 按 URL 去重

系统 SHALL 对 URL 做归一化（去除不标识文档的查询参数）后与已导入记录比对；命中已导入的文档 SHALL 跳过而非重复落盘。

#### Scenario: 重复导入同一文档链接

- **WHEN** 导入的某个文档 URL 归一化后已存在于文档记录中
- **THEN** 系统跳过该文档，标记为「已导入」，不重复落盘也不重复写库

### Requirement: 失败记录持久化与父子关系追溯

系统 SHALL 将递归抓取中失败的文档持久化到失败导入表（含 URL、标题、错误、父链接、深度、时间）。成功导入的文档 SHALL 在落盘 Markdown 头部与导入树记录中保留来源链接、父文档、子文档等关系。

#### Scenario: 子文档抓取失败被记录

- **WHEN** 递归过程中某个子文档抓取失败
- **THEN** 系统将该失败记录写入失败导入表，并通过进度事件上报 `failed`，不中断其余文档的抓取

#### Scenario: 保留父子关系

- **WHEN** 一个子文档成功导入且存在父文档
- **THEN** 系统在其 Markdown 头部写入 `> 父文档: <父标题>` 等关系行，并在导入树记录中保存完整父子结构与源链接

### Requirement: 企业微信 CLI API 读写文档

系统 SHALL 通过企业微信官方 CLI（`@wecom/cli`，凭证经 `wecom-cli init` 配置）读写企微在线文档，作为优先于无头浏览器抓取的方式。读取 SHALL 采用异步轮询（type=2 Markdown，task_id 轮询至 task_done）；写入 SHALL 支持创建文档并以 Markdown 覆写内容。在 Windows 上系统 SHALL 通过 `node <wecom.js>` 直接调用，绕开 `.cmd` 批处理包装器（其 `%*` 参数重展开会使 Markdown 特殊字符触发"命令行太长"等错误）。遇企微读取频率限制（errcode 851010/851000）系统 SHALL 退避重试，连续多次限流时判定配额耗尽并停止本批。

#### Scenario: 读取企微文档为干净 Markdown

- **WHEN** 对一个企微在线表格调用文档读取
- **THEN** 返回结构完整的 Markdown 表格，而非 protobuf 解码乱码

### Requirement: base64 内联图片落地

读取企微文档返回的 base64 内联图片 SHALL 被提取为本地文件（按内容 hash 命名存于 kb_images/），Markdown 中替换为本地服务路径 `/api/documents/kb-image/{name}`，避免单篇文档因内联 base64 膨胀到数十 MB。系统 SHALL 提供该路径的图片服务并防止目录穿越。

#### Scenario: 大图文档落地

- **WHEN** 一篇含多张图的文档以 base64 内联返回（数十 MB）
- **THEN** 图片落地为本地文件，Markdown 压缩到正文级大小，前端经 kb-image 路径正常显示

### Requirement: 知识库批量重导质量门禁

系统 SHALL 支持用企微 API 重读已有企微来源文档以替换旧的 Playwright 抓取内容。替换策略为"放宽门槛"：API 读取成功即默认替换，仅在明显倒退时拒绝（丢图、大量丢表格、内容近乎全失）并标记供人工复核。读取失败/超时/空内容 SHALL 跳过并保留原文，绝不以空内容覆盖。替换前原文 SHALL 永久备份到 knowledge_base_old/ 以支持新旧对比与回滚。重导 SHALL 支持续跑（跳过已替换）、单批上限、限流早停、失败计数（屡次限流的文档排到队尾），替换成功 SHALL 刷新文档上传时间。

#### Scenario: 读取失败不破坏原文

- **WHEN** 某文档 API 读取失败或返回空
- **THEN** 跳过该文档，保留原 Playwright 内容不变

### Requirement: 表格行级摘要（LLM）

通过企微 API 导入的文档，系统 SHALL 用 LLM 理解其中的表格（包括企微导出常见的"竖排碎片"——单元格被拆成多行、列对不齐的错乱结构），为每条数据记录生成一句"字段＝值"自然语言摘要，追加到文档末尾的专用块（`<!-- TABLE_SUMMARY_START -->`…`<!-- TABLE_SUMMARY_END -->`）。该处理 SHALL 幂等。每日重导管线导入新文档时 SHALL 自动应用；存量 API 导入文档 SHALL 全部补做。

#### Scenario: 竖排乱表生成可检索摘要

- **WHEN** 一篇文档含竖排碎片表格（如"资源名 iOSImage"与"大小 4.31MB"被拆在不同行）
- **THEN** LLM 生成"…资源名 iOSImage、大小 4.31MB、负责人 X"的行摘要，使该数据可被关键词检索命中

### Requirement: 每日重导与当日质量评测

知识库企微文档重导 SHALL 受配额约束按每日定时任务推进（串行、固定间隔、配额耗尽自动早停、resume 跳过已导）。每日重导后 SHALL 记录当日成功导入的文档清单，并对这批文档自动执行新旧对比评测（录入质量、检索效率、检索质量），结果汇总汇报。

#### Scenario: 当日新增文档自动评测

- **WHEN** 某日重导成功若干篇
- **THEN** 系统对当日这批文档跑新旧对比评测并汇报；当日 0 篇则跳过评测

### Requirement: Confluence 文档递归导入

系统 SHALL 支持导入 wiki.example.com Confluence 文档，按页面树（`/rest/api/content/{id}/child/page`）递归抓取根页面及其全部后代，去重并保留父子关系。info 文档中的图片 SHALL 通过携带登录 cookie 的服务端代理访问。

#### Scenario: 递归抓取页面树

- **WHEN** 给定一个 Confluence 根页面链接
- **THEN** 抓取根页面及所有子孙页面，标注父子归属，文件名清洗非法字符
