# Qlipoth — 基于 Agentic Search 的知识库系统

> 一个面向团队的知识库问答系统：文档以纯文本存储，查询时由 LLM 生成搜索策略、用 ripgrep 实时检索，再由 LLM 整合生成回答。零向量数据库依赖。

## 项目定位

Qlipoth 区别于传统 RAG（向量检索增强生成）方案，采用类似 Claude Code / Codex 的工作方式：

- **文件即知识**：上传文档转为 Markdown 文本存储，保持可读性与可搜索性
- **Agentic Search**：LLM 主动生成搜索策略（关键词、文件过滤），而非被动向量匹配
- **零向量数据库依赖**：无需 embedding 模型、向量库等额外基础设施
- **流式响应**：SSE 流式输出，1–2 秒可见首字
- **多模型兼容**：OpenAI / Anthropic 双 API 格式，支持 DeepSeek、通义千问、智谱、Moonshot、Ollama 等
- **多轮对话**：追问时指代消解 + 历史回灌，旁路设计不影响单问召回
- **权限隔离**：三级角色（管理员 / 超级用户 / 普通用户）

## 能力概览

- 文档上传与解析（docx / xlsx / pptx / pdf / md / txt，含 OCR）
- 在线文档链接导入（可插拔 provider，默认提供示例适配器）
- LLM 搜索策略生成 + ripgrep 检索 + IDF 覆盖度排序 + BM25 旁路重排（RRF 融合）
- 流式问答、引用文档、多轮上下文
- 文档标签体系与标签过滤检索
- 富文本渲染（marked + DOMPurify XSS 净化）
- 离线评测框架（golden set + Recall@K / MRR，配套合成示例数据）

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| 前端 | Vue 3 + Vite + Element Plus + Vue Router |
| 后端 | Python 3.12 + FastAPI + Uvicorn |
| 存储 | 本地文件系统（Markdown）+ SQLite（元数据 / 用户 / 历史） |
| 搜索 | ripgrep（JSON 模式）+ 关键词扩展 + IDF 排序 + BM25/RRF 融合 |
| LLM | OpenAI Chat Completions / Anthropic Messages（双格式兼容） |
| 文档解析 | python-docx / python-calamine / python-pptx / pypdf + Tesseract OCR |
| 认证 | Cookie Token + PBKDF2 密码哈希 + 三级角色 |

## 查询流程

```
用户提问
  → (追问时) 载入近 N 轮历史 → LLM 指代消解
  → LLM 生成搜索策略（关键词 + 文件过滤）
  → 关键词扩展 + ripgrep 检索 + IDF 排序 + BM25/RRF 融合
  → LLM 基于检索结果流式生成回答
  → 返回回答 + 引用文档
```

多轮对话为旁路设计：新会话首轮无历史时与单问行为一致。

## 安装运行

### 后端

```bash
cd backend
pip install -r requirements.txt          # 或按需安装依赖
cp .env.example .env                      # 填入 LLM API Key 等配置
py -3.12 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev        # 开发；npm run build 产出 dist/
```

### 配置项（环境变量 / 设置页）

| 配置 | 说明 |
|------|------|
| `LLM API Key / Base URL / Model` | 在设置页配置，仅管理员可改 |
| `QLIPOTH_CORS_ORIGINS` | 允许的前端 origin 白名单（逗号分隔），默认本地开发域名 |
| 在线文档 provider | 可插拔；公开版提供示例适配器，内部域名/凭据通过配置注入，代码默认不含任何真实内部地址 |

## 示例数据

`backend/eval/` 提供合成的评测框架与示例数据（虚构组织、虚构业务），用于演示检索评测流程，可直接运行：

```bash
cd backend
py -3.12 -m unittest eval.test_features eval.test_security
```

## 安全边界

- 所有业务接口服务端鉴权（未登录 401 / 越权 403），前端隐藏不作为安全边界
- 知识库文件读取限定在知识库目录内，防路径穿越
- 不可信文档渲染经 DOMPurify 净化，防 XSS
- 用户数据隔离：聊天历史按登录用户隔离，覆盖角色与用户间越权

## License

MIT
