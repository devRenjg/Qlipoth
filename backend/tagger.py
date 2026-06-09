"""上传时的标签处理：手动标签落库 + 基于内容自动打标（与离线复核同口径）。

单次 LLM 判定 10 个主题标签 + 事故/故障，手动标签(含活动标签 S赛/春晚)与自动标签合并。
供 routes/upload.py 在文档入库后调用，best-effort：LLM 失败不影响导入本身。
"""
import json
import aiosqlite

from database import DB_PATH
from llm import chat_completion
from searcher import read_file_content

CONTENT_LIMIT = 6000

TOPIC_SYSTEM = """你是知识库文档分类助手。给定一篇大型活动保障文档，从下面固定的标签集合中选出所有**确实贴合**的标签，可多选，宁缺毋滥（只在明显相关时才选）。

主题标签及定义：
- 业务需求：描述业务功能或产品需求
- 高可用保障：面向高可用的技术方案（PCU、降级、限流、架构演进、压测、容灾），以及流程/工具/机制类技术沉淀
- 成本：带宽、采购、服务器成本和预算，含技术/业务预算与第三方采购
- 安全：弹幕安全(审核/真实性/黑灰产/冲塔)、业务安全(风控/作弊/资损防护)、基础技术安全(攻击/防护)
- 直播体验：画质、清晰度、延迟、卡顿、首帧等播放体验
- 弹幕：弹幕玩法、弹幕技术方案、弹幕业务需求
- 红包：红包发放的技术方案、业务需求、玩法
- 模板与名单：导入/导出/批量模板、白名单、房间或账号列表、资源位表格、人员名单等纯数据物料
- 项目管理：日报、周报、排期、进度同步、预演规划、rundown、研发汇总、QA摸排、问题记录等执行跟踪类
- 接口与配置：接口协议/说明、配置方法、环境配置、接入文档、Mod说明、域名解析、埋点等技术参考

另外判断 incident：该文档是否记录了**重大事故/资损/严重故障/影响用户的线上问题**（从严，纯方案/需求/名单/排期不算）。

返回严格 JSON（不要 markdown 代码块）：
{"tags": ["项目管理"], "incident": false, "reason": "一句话依据"}
规则：tags 只能取自上面 10 个标签名，逐字匹配；都不贴合返回 {"tags": [], "incident": false, "reason": "..."}。"""

# 内容可自动判定的标签（10 主题 + 事故/故障）；活动标签 S赛/跨晚/春晚 不靠内容猜
TOPIC_TAGS = {"业务需求", "高可用保障", "成本", "安全", "直播体验", "弹幕", "红包",
              "模板与名单", "项目管理", "接口与配置"}
INCIDENT_TAG = "事故/故障"


def _parse_json(text: str) -> dict | None:
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


async def suggest_tags(name: str, content: str) -> list[str]:
    """基于标题+内容一次性判定 10 主题标签 + 事故/故障。失败返回 []（best-effort）。

    与离线复核(eval/recheck)同口径：单次 LLM 判全部主题 + incident，上传即一步到位。
    """
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
    if data.get("incident"):
        tags.append(INCIDENT_TAG)
    return tags


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
    """入库后给文档打标：手动标签(如活动标签 S赛/春晚) 与 内容自动打标(主题标签) **合并**生效。

    之前是"有手动就不自动"，导致带活动标签的文档拿不到主题标签、或子文档拿不到活动标签。
    现在两者并集：手动指定的标签必打，同时按内容补主题标签。Best-effort。
    返回最终生效的标签名列表。
    """
    manual = [t.strip() for t in (manual_tags or []) if t and t.strip()]
    if not content:
        content = read_file_content(name) or ""
    suggested = await suggest_tags(name, content)
    final = list(dict.fromkeys(manual + suggested))  # 去重保序，手动优先
    if final:
        return await apply_tags(doc_id, final)
    return []
