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
