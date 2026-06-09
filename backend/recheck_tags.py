"""复核指定时间批次文档的标签：LLM 评估现有主题标签准确性 + 是否该打事故/故障。
不动 S赛 等活动标签(那是确定的)。dry-run 打印，--write 应用(只增删主题标签+事故故障)。

用法: py -3.12 recheck_tags.py 2026-06-09           # dry-run
      py -3.12 recheck_tags.py 2026-06-09 --write
"""
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
import llm
from config import load_settings

DB = load_settings().db_path
KB = Path(load_settings().knowledge_base_dir)
DATE = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1][0].isdigit() else "2026-06-09"
WRITE = "--write" in sys.argv

# 可自动增删的标签集合（主题维度 + 事故/故障）；活动标签 S赛/跨晚/春晚 不动
TOPIC = ["业务需求", "高可用保障", "成本", "安全", "直播体验", "弹幕", "红包",
         "模板与名单", "项目管理", "接口与配置"]
INCIDENT = "事故/故障"
KEEP = {"S赛", "跨晚", "春晚"}  # 活动标签，复核不动

SYS = f"""你在复核知识库文档的标签是否准确。给定文档标题+内容，判定：
1. topics: 从这10个主题标签里选出**确实贴合**的(可多选可空，宁缺毋滥)：
   业务需求/高可用保障/成本/安全/直播体验/弹幕/红包/模板与名单/项目管理/接口与配置
   - 安全含弹幕安全/业务安全/技术安全
2. incident: 该文档是否记录了**重大事故/资损/严重故障/影响用户的线上问题**(true/false，从严)

返回严格JSON(不要markdown)：{{"topics":["项目管理"],"incident":false,"reason":"一句话"}}"""


async def judge(name, content, sem):
    async with sem:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": SYS},
                 {"role": "user", "content": f"标题：{name}\n\n内容：\n{content[:6000]}"}],
                temperature=0, model=load_settings().llm_model)
        except Exception:
            return None
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        return None
    return {"topics": [t for t in d.get("topics", []) if t in TOPIC],
            "incident": bool(d.get("incident")), "reason": d.get("reason", "")}


async def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        "SELECT id, original_name, stored_path FROM documents WHERE uploaded_at LIKE ?",
        (DATE + "%",)).fetchall()]
    print(f"{'[写库]' if WRITE else '[DRY-RUN]'} 复核 {DATE} 批次 {len(rows)} 篇\n", flush=True)
    tagid = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM tags").fetchall()}

    sem = asyncio.Semaphore(4)

    async def one(d):
        p = KB / d["stored_path"]
        content = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
        cur = {t["name"] for t in db.execute(
            "SELECT t.name FROM document_tags dt JOIN tags t ON t.id=dt.tag_id WHERE dt.document_id=?",
            (d["id"],)).fetchall()}
        res = await judge(d["original_name"], content, sem) if content else None
        return d, cur, res

    results = await asyncio.gather(*[one(d) for d in rows])

    from collections import Counter
    add_topic = Counter(); del_topic = Counter(); inc_n = 0
    plan = []
    for d, cur, res in results:
        if not res:
            continue
        cur_topic = {t for t in cur if t in TOPIC}
        want_topic = set(res["topics"])
        to_add = want_topic - cur_topic
        to_del = cur_topic - want_topic
        want_inc = res["incident"] and INCIDENT not in cur
        for t in to_add: add_topic[t] += 1
        for t in to_del: del_topic[t] += 1
        if want_inc: inc_n += 1
        if to_add or to_del or want_inc:
            plan.append((d, to_add, to_del, want_inc, res["reason"]))

    print(f"=== 复核结论 ===")
    print(f"建议新增主题标签: {dict(add_topic)}")
    print(f"建议删除主题标签: {dict(del_topic)}")
    print(f"建议打事故/故障: {inc_n} 篇\n")
    print("逐篇变更(前20):")
    for d, ta, td, wi, reason in plan[:20]:
        chg = []
        if ta: chg.append(f"+{list(ta)}")
        if td: chg.append(f"-{list(td)}")
        if wi: chg.append("+事故/故障")
        print(f"  {d['original_name'][:30]}: {' '.join(chg)} | {reason[:28]}")

    if WRITE:
        for d, ta, td, wi, _ in plan:
            for t in ta:
                db.execute("INSERT OR IGNORE INTO document_tags (document_id,tag_id) VALUES (?,?)", (d["id"], tagid[t]))
            for t in td:
                db.execute("DELETE FROM document_tags WHERE document_id=? AND tag_id=?", (d["id"], tagid[t]))
            if wi:
                db.execute("INSERT OR IGNORE INTO document_tags (document_id,tag_id) VALUES (?,?)", (d["id"], tagid[INCIDENT]))
        db.commit()
        print(f"\n已应用 {len(plan)} 篇的标签变更(活动标签未动)")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
