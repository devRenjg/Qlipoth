# 克里珀 - 大型活动保障知识库技术方案文档

> 当前版本：20260522.1  
> 最后更新：2026-05-22

---

## 版本历史

| 版本号 | 日期 | 变更内容 | 作者 |
|--------|------|----------|------|
| 20260519.1 | 2026-05-19 | 初始版本，完成核心架构搭建与基础功能实现 | Claude + User |
| 20260519.2 | 2026-05-19 | 流式输出、多模型兼容、性能分析、搜索优化、UI 重构 | Claude + User |
| 20260520.1 | 2026-05-20 | 人设体系、TAPD 集成、多文件覆盖优化、P0 自测用例 | Claude + User |
| 20260522.1 | 2026-05-22 | 企业微信文档链接导入、Playwright 抓取、递归嵌套导入、Sheet 支持、去重机制 | Claude + User |

---

## 1. 项目概述

克里珀（Qlipoth）是一个基于 Agentic Search 理念的大型活动保障知识库系统。区别于传统 RAG（检索增强生成）方案，采用类似 Claude Code / Codex 的工作方式：文件以纯文本形式存储在磁盘上，查询时通过 LLM 理解用户意图、生成搜索策略，再通过 grep + 文件读取实时搜索知识库内容，最后由 LLM 整合搜索结果生成自然语言回答。

### 核心理念

- **文件即知识**：上传的文档转为 Markdown 文本存储，保持可读性和可搜索性
- **Agentic Search**：LLM 主动生成搜索策略（关键词、文件过滤），而非被动向量匹配
- **流式响应**：SSE 流式输出，用户 1-2 秒即可看到首字，无需等待完整生成
- **多模型兼容**：支持 DeepSeek、通义千问、智谱、Moonshot、OpenAI、Anthropic 等主流模型
- **零向量数据库依赖**：不需要 embedding 模型、向量数据库等额外基础设施
- **在线文档直接导入**：支持企业微信文档/腾讯文档链接导入，Playwright 无头浏览器自动抓取

---

## 2. 技术架构

### 2.1 技术栈

| 层级 | 技术选型 |
|------|----------|
| 前端 | Vue 3 + Vite + Element Plus + Vue Router |
| 后端 | Python FastAPI + Uvicorn |
| 存储 | 本地文件系统（Markdown）+ SQLite（元数据 + 导入树） |
| 搜索 | grep（subprocess）+ Python fallback + 中文 bigram 分词 |
| LLM | OpenAI Chat Completions API / Anthropic Messages API（双格式兼容） |
| 文档解析 | python-docx / python-calamine / python-pptx |
| 在线文档抓取 | Playwright（Chromium）+ 网络拦截 + protobuf 解码 |
| 流式传输 | Server-Sent Events (SSE) |

### 2.2 系统架构

```
用户提问
  → Step 1: LLM 理解意图，生成搜索策略（关键词列表、文件过滤条件）
  → Step 2: 关键词 bigram 扩展 + grep 搜索 + 按 section 智能摘录
  → Step 3: LLM 基于搜索结果流式生成自然语言回答（SSE）
  → 实时返回回答 + 引用来源（文件名、行号）+ 耗时分析
```

### 2.3 数据流

**上传流程：**
用户上传文件 → 后端解析转为 Markdown → 存储到 knowledge_base/ 目录 → SQLite 记录元数据

**链接导入流程：**
用户输入 URL → Playwright 打开页面 → 拦截 opendoc/sheet API 响应 → protobuf 解码提取文本 → 递归发现子文档 → 逐个抓取 → 存储 + 记录层级关系

**查询流程：**
用户提问 → LLM 生成搜索策略 → grep 搜索知识库 → LLM 生成回答 → 返回结果 + 来源

---

## 3. 项目文件目录

```
C:/Code/Qlipoth/
├── backend/
│   ├── main.py                 # FastAPI 入口，CORS，路由注册，reload_dirs 配置
│   ├── config.py               # 配置管理（LLM API、路径、api_format 等）
│   ├── config.json             # 运行时配置文件（自动生成）
│   ├── database.py             # SQLite 数据库初始化（documents + import_trees 表）
│   ├── parsers.py              # 文档解析器（docx/xlsx/pptx/md/txt → Markdown，宽表拆分）
│   ├── searcher.py             # 搜索引擎（grep + bigram 扩展 + Windows 路径兼容 + fallback）
│   ├── scraper.py              # 在线文档抓取（Playwright + 网络拦截 + protobuf 解码 + 递归）
│   ├── llm.py                  # LLM 调用（OpenAI/Anthropic 双格式 + 流式输出 + 人设注入）
│   ├── Soul.md                 # 人设定义文件（研发负责人角色）
│   ├── requirements.txt        # Python 依赖
│   ├── metadata.db             # SQLite 数据库文件（自动生成）
│   ├── .browser_data/          # Playwright 浏览器持久化数据（登录态）
│   ├── knowledge_base/         # 转换后的文本文件存储目录
│   └── routes/
│       ├── __init__.py
│       ├── upload.py           # 文件上传 + 链接导入（递归 + 去重 + 树结构保存）
│       ├── query.py            # 查询接口（同步 + SSE 流式）+ 性能计时 + section 智能摘录
│       └── documents.py        # 文档管理 + 设置接口（含 api_format）
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js          # Vite 配置（代理 /api → 后端）
│   └── src/
│       ├── main.js             # Vue 入口
│       ├── App.vue             # 根组件（导航布局，白色主题）
│       ├── router.js           # 路由配置（含 /profiling）
│       ├── api/
│       │   └── index.js        # API 请求封装（axios + SSE fetch 流式）
│       ├── store/
│       │   └── profiling.js    # 性能分析数据 store（reactive）
│       └── views/
│           ├── Chat.vue        # 智能问答页（流式输出 + 预置问题 + 人设回答）
│           ├── Upload.vue      # 文件上传页（文件上传 + 链接导入 + 导入历史）
│           ├── Documents.vue   # 文档管理页（列表+详情）
│           ├── Profiling.vue   # 性能分析页（耗时分布图 + 瓶颈分析）
│           └── Settings.vue    # LLM 设置页（多模型预设 + API 格式切换）
├── test_p0.py                  # P0 端到端自测脚本
├── start.ps1                   # PowerShell 一键启动脚本
└── README.md                   # 本文档
```

---

## 4. 核心功能模块

### 4.1 文档上传与解析

支持格式：.docx, .xlsx, .xls, .pptx, .md, .txt

解析策略：
- **Word (.docx)**：提取段落文本、标题层级、表格内容，转为 Markdown
- **Excel (.xlsx)**：按 Sheet 提取，每个 Sheet 转为 Markdown 表格
- **PPT (.pptx)**：按 Slide 提取文本框内容
- **Markdown/TXT**：直接存储

### 4.2 在线文档链接导入（v20260522.1 新增）

支持企业微信文档（doc.weixin.qq.com）和腾讯文档（docs.qq.com）的 doc/sheet 类型。

**技术方案：**
- 使用 Playwright 启动系统 Chrome（复用用户登录态）
- 通过网络拦截捕获 `dop-api/opendoc`（doc）和 `dop-api/get/sheet`（sheet）接口响应
- Doc 格式：base64 → protobuf → UTF-8 文本提取
- Sheet 格式：base64 → zlib 解压 → protobuf → UTF-8 文本提取
- 支持递归抓取嵌套文档（最多 5 层深度），自动检测循环引用
- URL 去重：已导入的文档不会重复导入
- 每次导入保存完整的文档层级树结构

**嵌入链接识别：**
从 protobuf 文本中正则提取 `HYPERLINK https://doc.weixin.qq.com/...` 格式的嵌入链接。

### 4.3 Agentic Search 查询

两步 LLM 调用架构：
- **Step 1 - 意图理解**：LLM 分析用户问题，输出 JSON 搜索策略（关键词、文件过滤、是否需要全文）
- **Step 2 - 答案生成**：基于搜索结果，LLM 流式生成带引用来源的自然语言回答

搜索优化：
- **中文 bigram 扩展**：LLM 生成的复合关键词自动拆分为 2 字 bigram，提升 grep 命中率
- **Section 智能摘录**：大文件按 `##` 标题分 section，均匀分配上下文配额
- **Windows 路径兼容**：grep 输出解析正确处理 Windows 盘符

### 4.4 知识库关联体系

导入的文档在 Markdown 头部记录关联关系：
```markdown
# 文档标题

> 来源: URL
> 父文档: 主文档标题
> 子文档: 子文档A, 子文档B, 子文档C
```

搜索时可通过关联信息发现相关文档，增强知识检索的完整性。

---

## 5. v20260522.1 新增 Feature

### 5.1 企业微信文档链接导入

- **端点**：`POST /api/upload/url`
- **请求体**：`{ "url": "...", "recursive": true, "max_depth": 5 }`
- 支持 doc.weixin.qq.com、docs.qq.com、sheet.weixin.qq.com 等域名
- 使用系统 Chrome + persistent context 复用登录态
- 通过网络拦截获取文档数据（非 DOM 提取），稳定可靠

### 5.2 Sheet 文档支持

- Sheet 文档的 protobuf 数据经过 zlib 压缩，需额外解压步骤
- 提取表格中的文本内容（列标题、单元格数据、超链接文本）
- 与 doc 类型统一存储为 Markdown 格式

### 5.3 递归嵌套导入

- 自动识别文档中嵌入的子文档链接（HYPERLINK 格式）
- 递归抓取最多 5 层深度
- visited set 防止循环引用
- 同一 browser context 内顺序抓取，避免反复启动浏览器
- 已导入的 URL 自动跳过（不重复抓取）

### 5.4 文档去重机制

- documents 表新增 `source_url` 字段，记录标准化后的文档 URL
- 导入前检查 URL 是否已存在，存在则返回 409（单文档）或标记为 skipped（递归）
- URL 标准化：去除 query params，只保留 scheme + host + path

### 5.5 导入层级树持久化

- 新建 `import_trees` 表，每次导入保存完整的树结构 JSON
- `GET /api/upload/trees` 接口查询所有导入历史
- 前端上传页底部展示导入历史（Collapse 折叠 + 树形层级）

### 5.6 前端链接导入 UI

- 上传页改为 Tab 布局：「文件上传」+「链接导入」
- 链接导入支持递归选项（checkbox，默认勾选）
- 导入结果以树形层级展示（成功/跳过/失败三种状态）
- 导入历史区域展示所有历史导入记录及其文档树

---

## 6. 重点需求列表

| 优先级 | 需求 | 状态 | 备注 |
|--------|------|------|------|
| P0 | 文件上传与格式转换 | 已完成 | 支持 docx/xlsx/pptx/md/txt，宽表自动拆分 |
| P0 | Agentic Search 查询 | 已完成 | 两步 LLM 调用 + grep + bigram 扩展 |
| P0 | SSE 流式输出 | 已完成 | 首字 1-2s 可见，体感大幅提升 |
| P0 | 多模型兼容 | 已完成 | OpenAI/Anthropic 双格式，7 家预设 |
| P0 | 前端界面 | 已完成 | 问答/上传/管理/设置/性能分析 |
| P0 | 人设体系 | 已完成 | Soul.md 定义，研发负责人角色，自然语言风格 |
| P0 | P0 自测用例 | 已完成 | 3 个端到端用例，40s 性能基线 |
| P0 | 在线文档链接导入 | 已完成 | Playwright + 网络拦截 + protobuf 解码 |
| P0 | 递归嵌套导入 | 已完成 | 最多 5 层深度，循环检测，去重跳过 |
| P1 | TAPD 需求批量导入 | 已完成 | API 拉取 + 按簇拆分 + 索引生成 |
| P1 | 性能分析 Profiling | 已完成 | 各阶段耗时可视化 + 瓶颈定位 |
| P1 | Sheet 文档支持 | 已完成 | zlib + protobuf 解码，表格文本提取 |
| P1 | 文档去重 | 已完成 | source_url 字段，URL 标准化比对 |
| P1 | 导入层级树持久化 | 已完成 | import_trees 表 + 历史查询接口 + 前端展示 |
| P1 | 去除策略 LLM 调用 | 待开发 | 直接从问题提取关键词，省 6-7s |
| P1 | 上下文精简 | 待开发 | MAX_CONTEXT_CHARS 从 60K 降到 20-30K |
| P2 | 对话历史记录 | 待开发 | 保存历史问答，支持回溯 |
| P2 | 文档分类与标签 | 待开发 | 支持按分类组织知识库 |

---
<!-- PLACEHOLDER_DEPLOY -->

## 7. 部署与运行

**一键启动（PowerShell）：**
```powershell
.\start.ps1
```

**手动启动：**

后端：
```bash
cd backend
pip install -r requirements.txt
playwright install chromium  # 首次需要安装浏览器
python main.py
```

前端：
```bash
cd frontend
npm install
npm run dev
```

**访问地址：** http://localhost:3000

首次使用需在"设置"页配置 LLM API 信息（选择预设或手动填写 Base URL、API Key、模型名称、API 格式）。

链接导入功能首次使用时会弹出 Chrome 窗口，需在窗口中完成企业微信登录，之后登录态会持久化到 `backend/.browser_data/` 目录。

---

## 8. 性能基准（DeepSeek-chat，2026-05-19）

| 指标 | 数值 |
|------|------|
| 平均总耗时 | 26.1s |
| 搜索策略 LLM | 6.8s（26%） |
| grep 搜索 + 摘录 | 0.4s（2%） |
| 回答生成 LLM | 18.8s（72%） |
| 首字输出延迟（流式） | ~8s（策略 LLM + 搜索后即开始流式） |

**瓶颈**：LLM 调用占 98%，搜索本身极快。后续优化方向为去除策略 LLM 调用（-26%）和精简上下文（-30%）。
