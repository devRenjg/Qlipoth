"""上传时的标签处理：手动标签落库 + 未提供时基于内容自动打标。

复用 auto_tag.py / tag_misc.py 验证过的两段式分类：
  1) 7 个主题标签（多选，宁缺毋滥）
  2) 若主题标签全空，再从 3 个运营标签里强制选 1 个

供 routes/upload.py 在文档入库后调用，best-effort：LLM 失败不影响导入本身。
"""
import json
import aiosqlite

from database import DB_PATH
from llm import chat_completion
from searcher import read_file_content

CONTENT_LIMIT = 6000

TOPIC_SYSTEM = """你是知识库文档分类助手。给定一篇文档内容，从下面固定的标签集合中选出所有**确实贴合**该文档主题的标签，可多选，也可不选（宁缺毋滥，只在明显相关时才选）。

标签集合及定义：
- 业务需求：该文档主要描述一个业务功能或产品需求
- 高可用保障：技术方案文档，面向高可用保障，如直播PCU、降级、限流、架构演进、压测、容灾等
- 成本：与带宽、采购、服务器成本和预算相关，含技术/业务预算与第三方采购项
- 安全：涉及审核、风控、技术安全、业务安全相关领域
- 直播体验：涉及画质、清晰度、延迟、卡顿等播放体验相关的性能说明
- 弹幕：涉及弹幕玩法、弹幕技术方案、弹幕业务需求
- 红包：涉及发放红包的技术方案、业务需求、玩法等

返回严格的 JSON（不要 markdown 代码块）：
{"tags": ["标签1", "标签2"], "reason": "一句话说明依据"}

规则：
- tags 只能取自上面 7 个标签名，逐字匹配，不得新造标签。
- 一篇文档可能命中多个标签（如既是业务需求又涉及红包）。
- 若都不贴合，返回 {"tags": [], "reason": "..."}。"""

TOPIC_TAGS = {"业务需求", "高可用保障", "成本", "安全", "直播体验", "弹幕", "红包"}

OP_SYSTEM = """你是知识库文档归类助手。下面这篇文档不属于具体业务/技术主题，而是一份操作或参考类文档。请从以下 3 个类别中选出**最贴合**的一个：

- 模板与名单：各类导入/导出/批量模板、白名单、房间或账号列表、资源位表格、测试机统计等纯数据物料
- 项目管理：日报、排期、进度同步、预演规划、rundown、研发汇总、测试信息、QA摸排、问题记录等执行与跟踪类
- 接口与配置：接口协议/说明、配置方法/指南、环境配置、接入文档、Mod说明、域名解析、埋点/router等技术参考

返回严格 JSON（不要 markdown）：{"tag": "类别名", "reason": "一句话依据"}
规则：tag 必须逐字取自上面 3 个类别名之一，必须选一个最接近的，不得为空、不得新造。"""

OP_TAGS = {"模板与名单", "项目管理", "接口与配置"}


def _parse_json(text: str) -> dict | None:
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def suggest_tags(name: str, content: str) -> list[str]:
    """基于标题+内容推断标签名列表。失败返回 []（best-effort，不抛异常）。"""
    user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
    try:
        text, _ = await chat_completion(
            [{"role": "system", "content": TOPIC_SYSTEM}, {"role": "user", "content": user}],
            temperature=0,
        )
    except Exception:  # noqa: BLE001
        return []
    data = _parse_json(text) or {}
    tags = [t for t in data.get("tags", []) if t in TOPIC_TAGS]
    if tags:
        return tags

    # 主题标签全空 → 退到运营标签强制选 1 个
    try:
        text, _ = await chat_completion(
            [{"role": "system", "content": OP_SYSTEM}, {"role": "user", "content": user}],
            temperature=0,
        )
    except Exception:  # noqa: BLE001
        return []
    data = _parse_json(text) or {}
    tag = data.get("tag")
    return [tag] if tag in OP_TAGS else []


async def _resolve_tag_ids(db: aiosqlite.Connection, names: list[str]) -> list[int]:
    """标签名 → id，不存在则新建（满足"内容不匹配自己拟定标签"的诉求）。"""
    ids: list[int] = []
    for raw in names:
        n = (raw or "").strip()
        if not n:
            continue
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM tags WHERE name = ?", (n,))
        row = await cur.fetchone()
        if row:
            ids.append(row["id"])
        else:
            cur = await db.execute("INSERT INTO tags (name) VALUES (?)", (n,))
            ids.append(cur.lastrowid)
    return list(dict.fromkeys(ids))


async def apply_tags(doc_id: int, tag_names: list[str]) -> list[str]:
    """把标签名落库到 document_tags（新建缺失标签）。返回最终生效的标签名。"""
    names = [n.strip() for n in tag_names if n and n.strip()]
    if not names:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        ids = await _resolve_tag_ids(db, names)
        for tid in ids:
            await db.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                (doc_id, tid),
            )
        await db.commit()
    return names


async def tag_document(doc_id: int, name: str, content: str, manual_tags: list[str] | None = None) -> list[str]:
    """入库后给文档打标：有手动标签用手动；否则解析内容自动打标。Best-effort。

    返回最终生效的标签名列表（供前端/日志展示）。
    """
    manual = [t.strip() for t in (manual_tags or []) if t and t.strip()]
    if manual:
        return await apply_tags(doc_id, manual)
    if not content:
        content = read_file_content(name) or ""
    suggested = await suggest_tags(name, content)
    if suggested:
        return await apply_tags(doc_id, suggested)
    return []
