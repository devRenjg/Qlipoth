"""给现有保障清单条目回填 stage(保障阶段) + team/owner(归属)，不动正文与勾选。

针对已生成的清单(如 S赛 #2，109条)：逐条把"现象+建议+来源"喂 LLM，只判定
保障阶段与团队/负责人，回填新字段。现象/原因/处置/建议/勾选/已处理 全不改。

用法: py -3.12 backfill_checklist_meta.py <checklist_id>        # dry-run
      py -3.12 backfill_checklist_meta.py <checklist_id> --write
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
import llm
from config import load_settings

DB = load_settings().db_path
STAGES = ["备战前期", "压测演练", "上线前", "活动当天", "活动后", "未分类"]

SYS = f"""给定一条大型活动保障的"踩坑条目"，判定两件事：
1. stage 保障周期阶段(这个坑该在哪个阶段防范/发生)，从这些选一个：备战前期 / 压测演练 / 上线前 / 活动当天 / 活动后；判不出填"未分类"
2. team 负责团队、owner 负责人：仅当条目内容里明确体现归属时填，**不确定就留空字符串**，不要猜

返回严格 JSON(不要 markdown)：{{"stage": "压测演练", "team": "", "owner": ""}}
stage 必须取自上面六个值之一。"""


async def judge(item, sem):
    text = (f"维度：{item['dimension']}\n现象：{item['phenomenon']}\n"
            f"原因：{item.get('cause','')}\n建议：{item.get('suggestion','')}\n"
            f"时点：{item.get('timing','')}\n来源：{item.get('source_files','')}")
    async with sem:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": SYS}, {"role": "user", "content": text}],
                temperature=0, model=load_settings().llm_model)
        except Exception:
            return {"stage": "未分类", "team": "", "owner": ""}
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        return {"stage": "未分类", "team": "", "owner": ""}
    stage = d.get("stage", "未分类")
    return {"stage": stage if stage in STAGES else "未分类",
            "team": (d.get("team") or "").strip(), "owner": (d.get("owner") or "").strip()}


async def main():
    if len(sys.argv) < 2:
        print("用法: backfill_checklist_meta.py <checklist_id> [--write]")
        return
    cid = int(sys.argv[1])
    write = "--write" in sys.argv
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM checklist_items WHERE checklist_id=?", (cid,)).fetchall()]
    print(f"{'[写库]' if write else '[DRY-RUN]'} 清单#{cid}：{len(rows)} 条，逐条判 stage+team+owner（不动正文/勾选）\n", flush=True)

    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(*[judge(r, sem) for r in rows])

    from collections import Counter
    sc = Counter(r["stage"] for r in results)
    team_n = sum(1 for r in results if r["team"])
    owner_n = sum(1 for r in results if r["owner"])
    print("阶段分布:", dict(sc))
    print(f"判出团队 {team_n} 条，负责人 {owner_n} 条\n")
    for r, j in list(zip(rows, results))[:10]:
        print(f"  [{j['stage']}] team={j['team'] or '-'} owner={j['owner'] or '-'} | {r['phenomenon'][:32]}")

    if write:
        for r, j in zip(rows, results):
            con.execute("UPDATE checklist_items SET stage=?, team=?, owner=? WHERE id=?",
                        (j["stage"], j["team"], j["owner"], r["id"]))
        con.commit()
        print(f"\n已回填 {len(rows)} 条的 stage/team/owner（正文与勾选未动）")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
