"""一次性脚本：对当前无标签的"非主题型"文档归类到 3 个运营标签之一。
模板与名单 / 项目管理 / 接口与配置，单选。
"""
import asyncio
import json
import sqlite3

from config import load_settings
from llm import chat_completion
from searcher import read_file_content

CONTENT_LIMIT = 4000
CONCURRENCY = 4

SYSTEM = """你是知识库文档归类助手。下面这篇文档不属于具体业务/技术主题，而是一份操作或参考类文档。请从以下 3 个类别中选出**最贴合**的一个：

- 模板与名单：各类导入/导出/批量模板、白名单、房间或账号列表、资源位表格、测试机统计等纯数据物料
- 项目管理：日报、排期、进度同步、预演规划、rundown、研发汇总、测试信息、QA摸排、问题记录等执行与跟踪类
- 接口与配置：接口协议/说明、配置方法/指南、环境配置、接入文档、Mod说明、域名解析、埋点/router等技术参考

返回严格 JSON（不要 markdown）：{"tag": "类别名", "reason": "一句话依据"}
规则：tag 必须逐字取自上面 3 个类别名之一，必须选一个最接近的，不得为空、不得新造。"""

VALID = {"模板与名单", "项目管理", "接口与配置"}


async def classify(name, content):
    user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
    try:
        text, _ = await chat_completion(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            temperature=0,
        )
    except Exception as e:  # noqa: BLE001
        return None, f"LLM错误: {e}"
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, f"解析失败: {text[:80]}"
    tag = data.get("tag")
    return (tag if tag in VALID else None), data.get("reason", "")


async def main():
    db = sqlite3.connect(load_settings().db_path)
    db.row_factory = sqlite3.Row
    name_to_id = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM tags")}
    docs = db.execute(
        "SELECT id, original_name, stored_path FROM documents "
        "WHERE id NOT IN (SELECT DISTINCT document_id FROM document_tags) ORDER BY id"
    ).fetchall()
    print(f"待归类 {len(docs)} 篇，并发={CONCURRENCY}", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    counts = {t: 0 for t in VALID}
    done = 0

    async def worker(doc):
        nonlocal done
        async with sem:
            content = read_file_content(doc["stored_path"]) or doc["original_name"]
            tag, reason = await classify(doc["original_name"], content)
            done += 1
            print(f"[{done}/{len(docs)}] #{doc['id']} {doc['original_name'][:40]} -> {tag} ({reason[:50]})", flush=True)
            if tag:
                db.execute(
                    "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                    (doc["id"], name_to_id[tag]),
                )
                db.commit()
                counts[tag] += 1

    await asyncio.gather(*(worker(d) for d in docs))
    print("\n=== 归类统计 ===", flush=True)
    for t in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {t}: {counts[t]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
