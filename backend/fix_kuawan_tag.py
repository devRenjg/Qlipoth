"""严判"跨晚"标签：跨晚 = 跨年晚会(12月31日跨到1月1日)，与除夕春晚是两个不同活动。
当初打标把 CNY 项目文档普遍误打了跨晚。本脚本对所有带"跨晚"标签的文档重新严判，
只有真正涉及"跨年晚会(12/31)"内容的保留，否则去掉跨晚标。不动其他标签。

用法: py -3.12 fix_kuawan_tag.py            # dry-run
      py -3.12 fix_kuawan_tag.py --write     # 写库
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
WRITE = "--write" in sys.argv

SYS = """判断这篇文档是否真正涉及【跨年晚会】。

【关键定义，务必严格区分】
- 跨年晚会：每年 12月31日 跨到 1月1日 的那场晚会(俗称"跨晚")。
- 除夕春晚：春节除夕夜的春节联欢晚会。
- 这是两个完全不同的活动！CNY/春节档项目大量内容只服务"除夕春晚"，不要因为是CNY项目就算跨年晚会。

只有当文档内容**明确涉及12月31日的跨年晚会**(如跨年晚会的直播保障、跨晚专项、12.31当晚的玩法/技术等)时，才判定为"是"。
若文档只讲除夕春晚、或只是泛泛的CNY春节档内容而未specifically涉及跨年晚会，判定为"否"。

返回严格 JSON(不要 markdown)：{"is_kuawan": true 或 false, "reason": "一句话依据"}"""


async def judge(name, content, sem):
    async with sem:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": SYS},
                 {"role": "user", "content": f"文档标题：{name}\n\n文档内容：\n{content[:6000]}"}],
                temperature=0, model=load_settings().llm_model)
        except Exception:
            return None
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
        return {"keep": bool(d.get("is_kuawan")), "reason": d.get("reason", "")}
    except json.JSONDecodeError:
        return None


async def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    kid = db.execute("SELECT id FROM tags WHERE name='跨晚'").fetchone()["id"]
    rows = [dict(r) for r in db.execute(
        """SELECT d.id, d.original_name, d.stored_path FROM documents d
           JOIN document_tags dt ON d.id=dt.document_id WHERE dt.tag_id=?""", (kid,)).fetchall()]
    print(f"{'[写库]' if WRITE else '[DRY-RUN]'} 带跨晚标签 {len(rows)} 篇，逐篇严判\n", flush=True)

    sem = asyncio.Semaphore(4)
    done = {"n": 0}

    async def one(d):
        p = KB / d["stored_path"]
        content = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
        res = await judge(d["original_name"], content, sem) if content else {"keep": False, "reason": "无内容"}
        done["n"] += 1
        if done["n"] % 30 == 0:
            print(f"  ...{done['n']}/{len(rows)}", flush=True)
        return d, res

    results = await asyncio.gather(*[one(d) for d in rows])
    keep = [(d, r) for d, r in results if r and r["keep"]]
    drop = [(d, r) for d, r in results if r and not r["keep"]]
    print(f"\n保留跨晚: {len(keep)} 篇 | 去掉跨晚: {len(drop)} 篇")
    print("\n保留的(真涉及跨年晚会)样例:")
    for d, r in keep[:12]:
        print(f"  ✓ {d['original_name'][:32]} | {r['reason'][:30]}")
    print("\n去掉的样例:")
    for d, r in drop[:10]:
        print(f"  ✗ {d['original_name'][:32]} | {r['reason'][:30]}")

    if WRITE:
        for d, r in drop:
            db.execute("DELETE FROM document_tags WHERE document_id=? AND tag_id=?", (d["id"], kid))
        db.commit()
        left = db.execute("SELECT COUNT(*) FROM document_tags WHERE tag_id=?", (kid,)).fetchone()[0]
        print(f"\n已去掉 {len(drop)} 篇的跨晚标，现跨晚标签文档数: {left}")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
