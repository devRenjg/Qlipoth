"""全库扫描打"事故/故障"标签：判定每篇文档是否记录了重大事故/资损/严重问题。

文档级标签(与S赛/春晚同级)。判定从严：只有确实记录了线上事故、资损、严重故障、
重大问题复盘的文档才打；普通方案/需求/名单类不打。

用法: py -3.12 tag_incidents.py            # dry-run
      py -3.12 tag_incidents.py --write     # 写库
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
TAG = "事故/故障"
CONTENT_LIMIT = 6000

SYS = """判断这篇大型活动保障文档是否**记录了重大事故/资损/严重问题**(用于备战警醒)。

判定为"是"的情形：文档明确记录了线上事故、资损事件、严重故障、重大bug、
影响用户的问题复盘、故障演练暴露的严重问题等。
判定为"否"的情形：纯方案设计、需求文档、名单/模板、排期、未涉及实际问题的总结。

从严判断——只有确实承载"出过什么问题/事故"的内容才算是。

返回严格 JSON(不要 markdown)：{"incident": true 或 false, "reason": "一句话依据"}"""


async def judge(name, content, sem):
    async with sem:
        user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                temperature=0, model=load_settings().llm_model)
        except Exception:
            return None
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
        return {"incident": bool(d.get("incident")), "reason": d.get("reason", "")}
    except json.JSONDecodeError:
        return None


async def main():
    write = "--write" in sys.argv
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    tag_id = con.execute("SELECT id FROM tags WHERE name=?", (TAG,)).fetchone()
    assert tag_id, f"标签 {TAG} 未建"
    tag_id = tag_id["id"]

    docs = con.execute("SELECT id, original_name, stored_path FROM documents").fetchall()
    print(f"{'[写库]' if write else '[DRY-RUN]'} 扫描 {len(docs)} 篇判定事故/故障\n", flush=True)

    sem = asyncio.Semaphore(4)
    done = {"n": 0}

    async def _one(d):
        path = KB / d["stored_path"]
        content = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
        res = await judge(d["original_name"], content, sem) if content else None
        done["n"] += 1
        if done["n"] % 50 == 0:
            print(f"  ...{done['n']}/{len(docs)}", flush=True)
        return (d["id"], d["original_name"], res)

    results = await asyncio.gather(*[_one(d) for d in docs])
    hits = [(did, name, r) for did, name, r in results if r and r["incident"]]
    print(f"\n判定为事故/故障: {len(hits)} / {len(docs)} 篇")
    print("\n样例(前15):")
    for did, name, r in hits[:15]:
        print(f"  ✓ {name[:32]:<32} | {r['reason'][:34]}")

    if write:
        for did, name, r in hits:
            con.execute("INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?,?)",
                        (did, tag_id))
        con.commit()
        print(f"\n已为 {len(hits)} 篇打上「{TAG}」标签")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
