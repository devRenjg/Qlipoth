# 克里珀 - 大型活动保障知识库技术方案文档

> 当前版本：20260603.1  
> 最后更新：2026-06-03

---

## 版本历史

| 版本号 | 日期 | 变更内容 | 作者 |
|--------|------|----------|------|
| 20260603.1 | 2026-06-03 | 搜索排序两波优化（IDF 覆盖度打分）、分词器修复（数字+单位前缀拆分）、离线评测体系（golden set + Recall/MRR）、Claude 模型接入与对比 | Claude + User |
| 20260526.1 | 2026-05-26 | 用户体系、权限分层、ripgrep 搜索引擎、PDF/OCR 支持、聊天历史、UI 重构 | Claude + User |
| 20260522.1 | 2026-05-22 | 企业微信文档链接导入、Playwright 抓取、递归嵌套导入、Sheet 支持、去重机制 | Claude + User |
| 20260520.1 | 2026-05-20 | 人设体系、TAPD 集成、多文件覆盖优化、P0 自测用例 | Claude + User |
| 20260519.2 | 2026-05-19 | 流式输出、多模型兼容、性能分析、搜索优化、UI 重构 | Claude + User |
| 20260519.1 | 2026-05-19 | 初始版本，完成核心架构搭建与基础功能实现 | Claude + User |

---

## 1. 项目概述

克里珀（Qlipoth）是一个基于 Agentic Search 理念的大型活动保障知识库系统。区别于传统 RAG（检索增强生成）方案，采用类似 Claude Code / Codex 的工作方式：文件以纯文本形式存储在磁盘上，查询时通过 LLM 理解用户意图、生成搜索策略，再通过 ripgrep 实时搜索知识库内容，最后由 LLM 整合搜索结果生成自然语言回答。

### 核心理念

- **文件即知识**：上传的文档转为 Markdown 文本存储，保持可读性和可搜索性
- **Agentic Search**：LLM 主动生成搜索策略（关键词、文件过滤），而非被动向量匹配
- **流式响应**：SSE 流式输出，用户 1-2 秒即可看到首字，无需等待完整生成
- **多模型兼容**：支持 DeepSeek、通义千问、智谱、Moonshot、OpenAI、Anthropic 等主流模型
- **零向量数据库依赖**：不需要 embedding 模型、向量数据库等额外基础设施
- **在线文档直接导入**：支持企业微信文档/腾讯文档链接导入，Playwright 无头浏览器自动抓取
- **权限隔离**：三级角色体系（管理员/超级用户/普通用户），不同角色不同功能权限

---

## 2. 技术架构

### 2.1 技术栈

| 层级 | 技术选型 |
|------|----------|
| 前端 | Vue 3 + Vite + Element Plus + Vue Router |
| 后端 | Python 3.12 + FastAPI + Uvicorn |
| 存储 | 本地文件系统（Markdown）+ SQLite（元数据/用户/历史） |
| 搜索 | ripgrep（JSON 模式）+ 关键词扩展 + IDF 覆盖度排序 + Python fallback |
| LLM | OpenAI Chat Completions API / Anthropic Messages API（双格式兼容） |
| 文档解析 | python-docx / python-calamine / python-pptx / pypdf + Tesseract OCR |
| 在线文档抓取 | Playwright（Chromium）+ 网络拦截 + protobuf 解码 |
| 流式传输 | Server-Sent Events (SSE) |
| 认证 | Cookie Token + PBKDF2 密码哈希 + 角色权限 |

### 2.2 系统架构

```
用户登录（用户名+密码）
  → Cookie Token 自动登录（7天有效）
  → 角色权限校验

用户提问
  → Step 1: LLM 理解意图，生成搜索策略（精确关键词 + 文件过滤）
  → Step 2: 关键词扩展 + ripgrep JSON 搜索 + IDF 覆盖度文件排序 + 统计 section 优先摘录
  → Step 3: LLM 基于搜索结果流式生成回答（全局视角，先总体后明细）
  → 返回回答 + 引用文档链接（最多5个）+ 耗时分析
  → 自动保存到聊天历史
```

### 2.3 搜索引擎架构

```
用户问题 → LLM 生成 keywords[]（最小核心实体词，年份单列）
  → 关键词扩展（去尾变体 + 4字拆分 + 数字单位前缀剥离，过滤短数字）
  → ripgrep --json 逐词搜索（max-count=200/文件），记录每词命中文件数（DF）
  → 文件打分：Σ IDF(文件命中的不同关键词) × (1 + 文件名命中加权)
  → 取分数最高的 10 个文件
  → 上下文摘录：统计/总计 section 50% 预算优先 + 超预算截取不跳过
  → 60K 字符上下文 → LLM 回答
```

> 搜索方案的完整设计与离线评测见 [§9 搜索方案详细设计](#9-搜索方案详细设计)。

### 2.4 数据流

**文件上传流程：**
用户上传文件 → 解析（docx/xlsx/pptx/pdf/md/txt）→ 提取 @人名 → 存储 Markdown → 记录元数据 + 导入历史

**链接导入流程：**
用户输入 URL → Playwright 抓取 → protobuf 解码 → 保留链接/图片 → 递归子文档（最多3层）→ SSE 实时推送进度 → 失败记录持久化

**查询流程：**
用户提问 → LLM 搜索策略 → ripgrep 搜索 → 文件选择 + 摘录 → LLM 回答 → 引用文档 → 保存历史

---

## 3. 项目文件目录

```
C:/Code/Qlipoth/
├── backend/
│   ├── main.py                 # FastAPI 入口，CORS，路由注册
│   ├── auth.py                 # 用户认证（注册/登录/角色/Token）
│   ├── config.py               # 配置管理
│   ├── config.json             # 运行时配置文件
│   ├── database.py             # SQLite 初始化（documents/import_trees/users/chat_history/failed_imports）
│   ├── parsers.py              # 文档解析器（docx/xlsx/pptx/pdf+OCR/md/txt）
│   ├── searcher.py             # 搜索引擎（ripgrep JSON + 关键词扩展 + fallback）
│   ├── scraper.py              # 在线文档抓取（Playwright + protobuf + 递归 + 链接保留）
│   ├── llm.py                  # LLM 调用（双格式 + 流式 + 人设注入 + 搜索策略 prompt）
│   ├── Soul.md                 # 人设定义（活动保障知识库助手）
│   ├── requirements.txt        # Python 依赖
│   ├── metadata.db             # SQLite 数据库
│   ├── .browser_data/          # Playwright 浏览器持久化数据
│   ├── knowledge_base/         # 知识库文档存储
│   ├── eval/                   # 搜索离线评测体系
│   │   ├── golden_set.json     # 评测集（100 题 / 50 文档，分 5 类）
│   │   ├── generate_questions.py  # 基于知识库批量生成评测问题
│   │   ├── run_eval.py         # 复用生产搜索链路，计算 Recall@K/MRR/Precision
│   │   └── reports/            # 评测报告（按 tag + 时间戳，md + json）
│   └── routes/
│       ├── upload.py           # 文件上传 + 链接导入（SSE 流式）+ 失败记录 + 重试
│       ├── query.py            # 查询接口（SSE 流式）+ IDF 覆盖度文件排序 + 聊天历史 CRUD
│       └── documents.py        # 文档管理 + Markdown 渲染查看 + 设置
├── frontend/
│   ├── src/
│   │   ├── App.vue             # 根组件（登录/注册 + 角色导航 + 用户信息）
│   │   ├── router.js           # 路由配置
│   │   ├── api/index.js        # API 封装（REST + SSE + 认证）
│   │   ├── store/profiling.js  # 性能分析 store
│   │   └── views/
│   │       ├── Chat.vue        # 智能问答（左侧历史栏 + 主聊天区）
│   │       ├── Upload.vue      # 上传文档（文件/链接/失败重试 Tab）
│   │       ├── Documents.vue   # 文档管理
│   │       ├── Users.vue       # 用户管理（管理员专属）
│   │       ├── Profiling.vue   # 性能分析
│   │       └── Settings.vue    # LLM 设置
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── start.ps1                   # PowerShell 一键启动
└── README.md                   # 本文档
```

---

## 4. 核心功能模块

### 4.1 文档上传与解析

支持格式：.docx, .xlsx, .xls, .pptx, .pdf, .md, .txt

解析策略：
- **Word (.docx)**：提取段落文本、标题层级、表格内容，转为 Markdown
- **Excel (.xlsx)**：按 Sheet 提取，每个 Sheet 转为 Markdown 表格，宽表自动拆分
- **PPT (.pptx)**：按 Slide 提取文本框内容
- **PDF (.pdf)**：优先提取文本层；图片型 PDF 自动 OCR（Tesseract，支持中英文）
- **Markdown/TXT**：直接存储

附加处理：
- 自动提取 `@人名` 作为负责人元数据
- 文件去重（同名文件不重复导入）
- 导入历史持久化

### 4.2 在线文档链接导入

支持企业微信文档（doc.weixin.qq.com）和腾讯文档（docs.qq.com）。

**技术方案：**
- Playwright 启动系统 Chrome（复用用户登录态）
- 网络拦截捕获 `dop-api/opendoc`（doc）和 `dop-api/get/sheet`（sheet）接口响应
- protobuf 解码提取文本，保留 HYPERLINK 转为 Markdown 链接格式
- 递归抓取嵌套文档（用户可控 0-3 层），SSE 实时推送进度
- 父文档已导入不阻断，子文档照常录入
- 失败记录持久化，支持后续重试
- 导入历史记录完整父子关系和源链接

### 4.3 Agentic Search 查询

**搜索引擎（ripgrep）：**
- 关键词扩展：3+字中文加去尾变体（覆盖率→覆盖），4字拆半，数字+单位前缀剥离（26年春晚→春晚），过滤短数字
- IDF 覆盖度排序：按"文件命中的不同关键词"累加 IDF，命中稀有词一次胜过命中宽泛词多次
- 文件名加权：文件名含原始关键词时整体加权，稳住"文件名即强信号"
- 上下文摘录：统计/总计 section 50% 预算优先，超预算截取不跳过
- 每关键词 max-count=200，避免大文件噪音

> 排序算法的演进、踩坑与离线评测数据见 [§9 搜索方案详细设计](#9-搜索方案详细设计)。

**LLM 回答：**
- 人设：大型活动保障知识库助手，全局视角
- 先总体后明细，不局限于某个部门
- 引用文档：最多5个，在线链接可跳转，本地文档可打开 Markdown 渲染页

### 4.4 用户体系与权限

**三级角色：**
- **admin（超级管理员）**：所有功能 + 用户管理
- **super（超级用户）**：智能问答 + 上传文档 + 文档管理 + 查看所有人问答历史
- **user（普通用户）**：智能问答 + 只看自己的问答历史

**认证机制：**
- 用户名 + 密码注册/登录
- 密码要求：8位以上，含大小写字母、数字、特殊字符
- 数据库存储 PBKDF2-SHA256 哈希 + 随机盐
- Cookie Token 自动登录（7天有效）
- 未登录无法使用系统

### 4.5 聊天历史

- 每次问答自动保存（问题 + 回答 + 引用文档 + 时间 + 用户）
- Chat 页面左侧栏展示历史列表，按时间倒序
- 点击历史项回显该条问答
- admin/super 可看所有人历史，普通用户只看自己的

### 4.6 失败重试

- 递归抓取中失败的文档自动记录到 `failed_imports` 表
- 上传文档页「失败重试」Tab（管理员可见）
- 支持单选/全选批量重试
- 成功或因重复跳过的自动从失败列表移除

---

## 5. v20260526.1 新增 Feature

| Feature | 说明 |
|---------|------|
| ripgrep 搜索引擎 | 替换原生 grep，JSON 模式输出，速度提升 2-5x |
| priority 文件选择 | 精确中文关键词命中的文件保证入选上下文 |
| 统计 section 优先 | 大文件中"统计/总计"标题的段落优先包含 |
| PDF 支持 | pypdf 文本提取 + Tesseract OCR（图片型 PDF） |
| 用户注册/登录 | 用户名+密码，PBKDF2 哈希，Cookie 7天自动登录 |
| 三级权限 | admin/super/user，导航和功能按角色隔离 |
| 用户管理页 | 管理员查看所有用户、修改角色、删除用户 |
| 聊天历史 | 自动保存问答，左侧栏展示，按角色控制可见范围 |
| 失败记录持久化 | 抓取失败的文档 URL 持久化，支持批量重试 |
| UI 重构 | 左侧历史栏 + 主聊天区布局，登录页居中 |
| 引用文档增强 | 在线链接蓝色跳转，本地文档绿色打开 Markdown 渲染页 |
| @人名 Owner 提取 | 文档中 @人名 自动提取为负责人元数据 |
| 递归层数用户可控 | 0-3 层，输入框选择 |
| 北京时间统一 | 所有时间戳显式使用 UTC+8 |

### v20260603.1 新增 Feature

| Feature | 说明 |
|---------|------|
| IDF 覆盖度排序 | 按文件命中的不同关键词累加 IDF，修正旧版被巨型文档统治的问题 |
| 分词器修复 | 数字+单位前缀复合词剥离（26年春晚→春晚），恢复核心实体召回 |
| 搜索策略 prompt 优化 | 强制拆最小核心实体词、年份单列，提升关键词质量与稳定性 |
| 离线评测体系 | golden set（100 题/50 文档）+ Recall@K/MRR/Precision，复用生产搜索链路 |
| Claude 模型接入 | Anthropic Messages API 双格式兼容，新增 DeepSeek vs Claude 评测对比 |

---

## 6. 重点需求列表

| 优先级 | 需求 | 状态 |
|--------|------|------|
| P0 | 文件上传与格式转换（含 PDF OCR） | 已完成 |
| P0 | Agentic Search 查询（ripgrep + priority） | 已完成 |
| P0 | SSE 流式输出 | 已完成 |
| P0 | 多模型兼容（OpenAI/Anthropic 双格式） | 已完成 |
| P0 | 用户注册/登录/权限体系 | 已完成 |
| P0 | 搜索排序优化 + 离线评测体系 | 已完成 |
| P0 | 人设体系（全局视角，先总体后明细） | 已完成 |
| P0 | 在线文档链接导入（递归 + SSE 进度） | 已完成 |
| P0 | 聊天历史持久化 | 已完成 |
| P1 | 失败记录持久化 + 批量重试 | 已完成 |
| P1 | 用户管理（角色修改/删除） | 已完成 |
| P1 | @人名 Owner 提取 | 已完成 |
| P1 | 引用文档链接（在线+本地） | 已完成 |
| P1 | 导入历史（父子关系 + 源链接） | 已完成 |
| P2 | 企微扫码登录 | 待开发（需企微应用权限） |
| P2 | 文档分类与标签 | 待开发 |
| P2 | 多轮对话上下文 | 待开发 |

---
<!-- PLACEHOLDER_DEPLOY -->

## 7. 部署与运行

**环境要求：**
- Python 3.12+
- Node.js 18+
- ripgrep（`winget install BurntSushi.ripgrep.MSVC`）
- Tesseract OCR（`winget install UB-Mannheim.TesseractOCR`）+ 中文语言包
- Google Chrome（Playwright 抓取用）

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

**首次使用：**
1. 用预置管理员账号登录
2. 在"设置"页配置 LLM API 信息
3. 链接导入首次使用时会弹出 Chrome 窗口，需完成企业微信登录

---

## 8. 性能基准（DeepSeek-v4-pro，2026-05-26）

| 指标 | 数值 |
|------|------|
| 平均总耗时 | ~20s |
| 搜索策略 LLM | ~7s |
| ripgrep 搜索 + 摘录 | ~0.1s |
| 回答生成 LLM（流式） | ~12s |
| 首字输出延迟 | ~8s（策略 LLM + 搜索后即开始流式） |

**优化点**：ripgrep 替换 grep 后搜索阶段从 0.4s 降到 0.1s；IDF 覆盖度排序确保稀有关键词命中的文件不被巨型文档淹没；统计 section 优先确保数量问题能拿到汇总数据。

---

## 9. 搜索方案详细设计

### 9.1 为什么不用向量检索

传统 RAG 需要 embedding 模型 + 向量数据库，引入额外基础设施、切块策略调参与索引重建成本，且对"@人名负责人""统计总计"这类结构化、数值型问题召回不稳。克里珀改用 Agentic Search：把"理解意图 → 决定搜什么 → 整合答案"交给 LLM，把"在全库精确定位文本"交给 ripgrep。知识库纯文本落盘，新增文档零索引成本，问题可解释、可调试。

### 9.2 搜索链路四段

```
LLM 搜索策略 → 关键词扩展 → ripgrep 逐词搜索 → 文件打分排序 → section 摘录 → LLM 回答
```

**① LLM 搜索策略（llm.py: SEARCH_STRATEGY_PROMPT）**

LLM 输出 `keywords[]` + `file_pattern` + `need_full_file`。Prompt 的关键约束：
- 拆最小核心实体词，不带年份/序数前缀（"26年春晚"→"春晚"）。长复合词会让召回率骤降。
- 涉及年份/同比/对比时，把年份单独列为关键词（2025、2026），因为数据常与年份并列。
- 数量问题（"多少""几个""人数"）补充"总计/统计/合计"等汇总词。
- "谁负责 XX"类问题，结果中 @ 后的人名即负责人。

**② 关键词扩展（searcher.py: `_expand_keywords`）**

ripgrep 是字面匹配，中文无分词，故需人工扩展：
- 3+ 字中文加去尾变体：覆盖率 → 覆盖
- 4 字中文拆两半：版本覆盖 → 版本、覆盖
- 数字+单位前缀剥离：26年春晚 → 春晚（剥离"年"），25届春晚 → 春晚（单位字集合 `年月日届期季版号周次轮`）
- 过滤 ≤2 位纯数字，避免噪音

> 分词器踩坑：早期版本对 `26年春晚` 只产出 `['26年春晚','年春']`，核心词"春晚"被时间前缀粘连而丢失，导致该类问题持续答错。修复后产出 `['26年春晚','春晚','年春']`，核心实体得以召回。

**③ ripgrep 逐词搜索（searcher.py: `_rg_search` / `grep_search`）**

每个扩展词跑一次 `rg --json --ignore-case --max-count 200`。max-count 限制单文件命中数，避免巨型文档刷屏。搜索时记录每个词命中的不同文件数（DF, document frequency），供 IDF 使用。ripgrep 不可用时回退纯 Python 遍历（`_fallback_search`）。

**④ 文件打分排序（query.py: `_select_files`）**

这是两波优化的核心。打分公式：

```
score(file) = Σ_kw IDF(kw) × (1 + filename_hit(file))
其中 kw 遍历"该文件命中的不同关键词"
IDF(kw) = log((N+1)/(DF(kw)+1)) + 0.5    # N 为知识库 .md 总数（60s 缓存）
filename_hit：文件名含原始关键词 → +2.0；含去尾变体 → +0.8
```

关键设计：**按"文件命中的不同关键词"累加 IDF，而非按命中行累加**。命中稀有词（如"版本覆盖率"，全库仅 3 篇）一次，远胜命中宽泛词（如"春晚"，263 篇）五十次。这正是修正旧版"按命中行累加被巨型文档统治"的要害。

### 9.3 排序算法演进（两波优化）

| 阶段 | 排序策略 | Recall@10 | MRR |
|------|----------|-----------|-----|
| baseline | priority 文件前5 + 命中数排序 | 0.43 | 0.24 |
| wave-1 | 纯 IDF 按命中行累加 | 0.37（回退） | — |
| wave-1b | priority + 文件名加权 | 0.54 | 0.34 |
| wave-2 | **IDF 覆盖度 + 文件名加权** | **0.72** | **0.44** |

> wave-1 的纯 IDF 按命中行累加反而劣化（用固定关键词离线 A/B 验证 0.43→0.37），原因是巨型文档命中行多、累加分高。wave-2 改为按"不同关键词"累加 IDF 后，命中率与 MRR 同步跃升。

### 9.4 离线评测体系（eval/）

为避免"凭感觉调参"，搭建可复现的离线评测：

- **golden_set.json**：100 题 / 50 文档，分排查类、数量类、方案类、歧义类、负责人类 5 种，每题标注期望命中文档。
- **run_eval.py**：直接复用生产链路（`generate_search_strategy` → `grep_search` → `_select_files`），保证评测口径与线上一致。指标含 Recall@K、MRR、Precision@K、全命中率，按类型分组输出。`elapsed` 仅计搜索链路耗时（策略 LLM + ripgrep + 文件选择，不含回答生成），即"搜索效率"。
- **reports/**：每次跑带 tag + 时间戳，落 md + json，便于版本间 A/B 对比。

### 9.5 DeepSeek vs Claude 评测对比（wave-2，同一 golden set）

| 指标 | DeepSeek-v4-pro | Claude（opus-4-8） |
|------|-----------------|-------------------|
| Recall@10 | 0.72 | 0.7245 |
| MRR | 0.4403 | 0.4675 |
| Precision@10 | 0.081 | 0.0768 |
| 搜索链路总耗时 | 205.5s | 236.3s |
| 评测中失败数 | 0 | 2（502 网关，非模型问题） |

结论：
- **召回基本持平**（0.72 vs 0.72），说明召回上限主要由关键词扩展 + 排序决定，而非模型。
- **Claude 排序质量更优**（MRR 0.44→0.47），意图较重的方案类、歧义类提升明显（方案类 Recall 0.6→0.8，歧义类 0.67→0.83），得益于关键词更稳定、更贴合核心实体。
- **DeepSeek 更快且更稳**（耗时低 ~13%，本轮零失败）。Claude 的 2 次失败为 502 网关抖动，属基础设施而非模型能力。
- 选型权衡：追求极致排序质量用 Claude，追求速度/成本/稳定用 DeepSeek，二者召回相当。
