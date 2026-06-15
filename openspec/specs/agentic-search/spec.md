# agentic-search Specification

## Purpose
以 Agentic Search（LLM 生成搜索策略 + ripgrep 实时全库搜索 + IDF 覆盖度文件排序 + section 摘录 + LLM 流式整合）替代向量检索，回答用户对知识库的提问，并通过离线评测体系（golden set + Recall/MRR）持续度量与优化搜索质量。
## Requirements
### Requirement: LLM 生成搜索策略

系统 SHALL 先调用 LLM 将用户问题转化为搜索策略，输出关键词列表（`keywords`）、文件名匹配模式（`file_pattern`）与是否需要整文（`need_full_file`）。策略 SHALL 拆出最小核心实体词（不带年份/序数前缀），对涉及年份/对比的问题单列年份关键词，对数量类问题补充「总计/统计/合计」等汇总词。

#### Scenario: 拆解最小核心实体词

- **WHEN** 用户问题含「26年春晚」这类带时间前缀的复合词
- **THEN** 搜索策略将其拆为核心实体「春晚」作为关键词，而非整体保留长复合词

#### Scenario: 数量类问题补充汇总词

- **WHEN** 用户问题包含「多少」「几个」「人数」等数量意图
- **THEN** 搜索策略的关键词中补充「总计」「统计」「合计」等汇总词

### Requirement: 关键词扩展

由于 ripgrep 为字面匹配且中文无分词，系统 SHALL 对 LLM 给出的关键词做人工扩展：3+ 字中文加去尾变体，4 字中文拆两半，数字+单位前缀复合词剥离单位取核心实体，过滤 ≤2 位纯数字噪音。

#### Scenario: 去尾变体扩展

- **WHEN** 关键词为「覆盖率」
- **THEN** 扩展结果包含原词「覆盖率」及去尾变体「覆盖」

#### Scenario: 数字单位前缀剥离

- **WHEN** 关键词为「26年春晚」
- **THEN** 扩展结果剥离时间前缀产出核心实体「春晚」，使核心词不被前缀粘连而漏召

### Requirement: ripgrep 逐词搜索与回退

系统 SHALL 对每个扩展关键词执行 `ripgrep --json --ignore-case --max-count 200` 全库搜索，记录每词命中的不同文件数（DF）。当 ripgrep 不可用时，系统 SHALL 回退到纯 Python 遍历搜索，保证搜索可用。

#### Scenario: 单文件命中数上限

- **WHEN** 某个关键词在一个超大文档中命中数百次
- **THEN** ripgrep 以 `max-count=200` 限制单文件命中数，避免巨型文档刷屏

#### Scenario: ripgrep 不可用时回退

- **WHEN** ripgrep 二进制不可用或调用失败
- **THEN** 系统回退到纯 Python 遍历逐行匹配，仍返回搜索结果

### Requirement: IDF 覆盖度文件排序

系统 SHALL 按「文件命中的不同关键词」累加 IDF 对文件打分，而非按命中行累加：`score(file) = Σ_kw IDF(kw) × (1 + filename_hit)`，其中 `IDF(kw) = log((N+1)/(DF(kw)+1)) + 0.5`，文件名含原始关键词时加权。系统 SHALL 取分数最高的至多 10 个文件进入上下文。

#### Scenario: 稀有词命中胜过宽泛词高频命中

- **WHEN** 文件 A 命中一次稀有词（全库仅数篇含有），文件 B 多次命中宽泛词（全库数百篇含有）
- **THEN** 按不同关键词累加 IDF 后，文件 A 的覆盖度得分高于文件 B，优先入选上下文

#### Scenario: 文件名命中加权

- **WHEN** 某文件的文件名包含原始关键词
- **THEN** 该文件得分整体加权（含原词 +2.0、含去尾变体 +0.8），稳住「文件名即强信号」的收益

### Requirement: section 摘录与上下文预算

当入选文件超出单文件预算时，系统 SHALL 按 section（`##` 标题）摘录命中附近内容，并给「统计/总计/汇总/合计/概览/总结」类 section 分配 50% 预算优先，超预算时截断而非整段跳过。总上下文 SHALL 受 `MAX_CONTEXT_CHARS`（60K 字符）约束。

#### Scenario: 统计 section 优先摘录

- **WHEN** 一个大文件含「统计/总计」类标题段落
- **THEN** 摘录时该 section 获得 50% 预算优先包含，确保数量类问题能拿到汇总数据

### Requirement: SSE 流式回答与耗时分析

系统 SHALL 以 Server-Sent Events 先推送元信息（引用来源、搜索策略、各阶段耗时），再流式推送 LLM 回答分片，最后推送总耗时。流式 SHALL 让用户在秒级看到首字。

#### Scenario: 流式查询的事件序列

- **WHEN** 用户发起流式查询
- **THEN** 系统先发 `meta` 事件（sources/strategy/timing），随后连续发 `chunk` 回答分片，结束发 `done` 含总耗时

### Requirement: 引用文档输出

系统 SHALL 在回答时输出至多 5 个引用来源；对链接导入的文档返回可跳转的在线链接，对本地上传文档返回可打开 Markdown 渲染页的本地引用。

#### Scenario: 在线文档引用返回链接

- **WHEN** 入选上下文的文档来自在线链接导入（头部含 `> 来源: <url>`）
- **THEN** 该引用项返回对应的在线 URL，供前端蓝色跳转

#### Scenario: 本地文档引用返回本地引用

- **WHEN** 入选上下文的文档为本地上传（无在线来源链接）
- **THEN** 该引用项返回本地文件引用，供前端打开 Markdown 渲染查看页

### Requirement: 离线评测体系

系统 SHALL 提供可复现的离线评测：基于 golden set（标注期望命中文档的问题集）复用生产搜索链路（策略生成 → ripgrep 搜索 → 文件选择），计算 Recall@K、MRR、Precision@K 与全命中率并分类型输出，评测报告 SHALL 带 tag + 时间戳落 md + json 以支持版本间 A/B 对比。

#### Scenario: 复用生产链路评测

- **WHEN** 运行离线评测
- **THEN** 评测直接调用与线上一致的 `generate_search_strategy → grep_search → _select_files`，对每题计算 Recall@K/MRR/Precision@K，按问题类型分组汇总

#### Scenario: 评测报告留档可对比

- **WHEN** 一次评测运行完成
- **THEN** 系统以 tag + 时间戳生成 md + json 报告落 `eval/reports/`，便于不同排序版本/模型之间 A/B 对照

### Requirement: BM25 + RRF 旁路重排

系统 SHALL 在 IDF 覆盖度文件排序之后，以零依赖的 char-bigram BM25（Okapi，k1=1.5/b=0.75）对候选文件重新打分，并用 RRF（k=60）将 BM25 排序与既有 grep/IDF 排序融合，提升召回。融合 SHALL 为旁路设计：当 BM25 不可用、异常或返回空时，系统 SHALL 回退到既有 `_select_files` 顺序，保证缺省零回归。被 BM25 召回但无 grep 命中行的文件，系统 SHALL 读取其开头若干预算字符以兑现召回增益。

#### Scenario: BM25/RRF 融合提升召回

- **WHEN** 候选文件经 IDF 覆盖度排序后进入 BM25 + RRF 融合
- **THEN** 系统输出融合后的文件顺序，使字面/词频相关但 IDF 排序靠后的文档有机会进入上下文

#### Scenario: BM25 异常时回退零回归

- **WHEN** BM25 模块不可用、抛异常或返回空结果
- **THEN** 系统回退到既有 `_select_files` 顺序，文件选择与未启用融合时完全一致

#### Scenario: 召回文件无命中行时读开头

- **WHEN** 某文件由 BM25 召回但在 grep 结果中无命中行
- **THEN** 系统读取该文件开头的预算字符纳入上下文，避免丢失召回增益

### Requirement: 相关图片画廊

系统 SHALL 从入选 LLM 上下文的 top 相关文档正文中抽取干净的内容图 URL，去重后随查询响应与 SSE `meta` 事件以 `relevant_images` 字段下发，供前端在答案旁展示相关图片。抽取 SHALL 仅识别已知图床的内容图（带类型参数或显式图片扩展名的 URL），以排除头像与解码残片乱码。抽取 SHALL 限制单篇文档与总计的图片数量以保证跨文档多样性。该抽取 SHALL 为旁路副作用：不改变喂给 LLM 的上下文文本、文件排序与回答内容。

#### Scenario: 答案带出相关图片

- **WHEN** 用户查询命中的 top 相关文档正文含已知图床的内容图
- **THEN** 系统在响应 / `meta` 的 `relevant_images` 中返回去重后的图片 URL 及其来源文档标题，前端在答案下方渲染缩略图画廊

#### Scenario: 排除头像与乱码

- **WHEN** 文档正文含头像 URL 或 protobuf 解码残片中的非图片乱码
- **THEN** 抽取仅保留带 `type=image/` 参数或显式图片扩展名的内容图 URL，不纳入头像与乱码片段

#### Scenario: 单篇限额保证多样性

- **WHEN** 某一图多文档被选为最相关来源
- **THEN** 抽取对该文档施加单篇图片数上限，使画廊覆盖多个来源文档而非被单篇独占

#### Scenario: 抽图不影响回答

- **WHEN** 启用相关图片抽取
- **THEN** 喂给 LLM 的上下文文本、文件排序与离线评测结果与未启用时一致，图片仅经独立的 `relevant_images` 字段旁路下发


### Requirement: 摘录密度优先与表格原子性

系统在把命中文档组装为 LLM 上下文时，当某文件命中行过多、预算不足以容纳全部命中时，SHALL 按"片段覆盖的不同查询关键词数"降序优先纳入（命中多个关键词的高密度行——如表格行级摘要——优先进入上下文），取满预算后按原文顺序还原以保证可读性，而非按行号先到先得导致靠后的目标行被挤出。标题含"统计/汇总/总结/概览/摘要"等的区块 SHALL 识别为高优先级 summary section 并分配更充足预算。命中落在 Markdown 表格内的，SHALL 纳入整张表（表格原子性）。

#### Scenario: 目标数据在长文档末尾的摘要区

- **WHEN** 查询命中某长文档上百行，目标数据位于文档末尾的"表格数据摘要"区块
- **THEN** 该摘要行因覆盖最多查询关键词被优先摘录进上下文，而非因排在后面被预算挤掉

### Requirement: 关键词检索健壮性

检索入口 `grep_search` 接收的关键词列表 SHALL 容忍非字符串元素（LLM 生成的搜索策略偶发返回 dict 或嵌套 list）：系统 SHALL 把每个关键词统一强制为字符串（dict 取 keyword/word 字段、list/tuple 拼接、其余 str() 兜底），过滤空值后再检索，绝不因关键词类型异常导致问答请求崩溃。

#### Scenario: LLM 返回 dict 形式关键词

- **WHEN** 搜索策略生成的 keywords 中混入 `{"keyword": "春晚"}` 这类 dict
- **THEN** 系统将其规整为字符串 "春晚" 继续检索，而非抛 AttributeError 使请求失败
