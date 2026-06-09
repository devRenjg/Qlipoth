"""原地改造保障清单：回填 severity + 把"可复用沉淀"重分配到其余6维。
不动 现象/原因/建议/负责人/勾选/来源。

用法: py -3.12 upgrade_cl_meta.py <checklist_id>          # dry-run
      py -3.12 upgrade_cl_meta.py <checklist_id> --write
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

CID = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 5
DIMS = ["事故/故障", "高可用保障", "直播播放体验", "安全", "业务需求", "成本"]
SEVS = ["P0", "P1", "P2"]
db = sqlite3.connect(load_settings().db_path)
db.row_factory = sqlite3.Row

SYS = """给定一条大型活动保障的踩坑条目，判定两件事：
1. severity 严重度: P0(重大事故/资损/严重故障) / P1(重要,需重点关注) / P2(一般改进项)
2. dimension 归到哪个维度(六选一): 事故/故障、高可用保障、直播播放体验、安全、业务需求、成本
   规则: 安全含弹幕安全/业务安全/技术安全; 运营/排期/项目协调归业务需求; 流程/工具/机制技术沉淀归高可用保障
返回严格JSON(不要markdown): {"severity":"P1","dimension":"高可用保障"}"""


async def judge(it, sem, reassign):
    text = f"现象：{it['phenomenon']}\n原因：{it.get('cause','')}\n建议：{it.get('suggestion','')}"
    async with sem:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": SYS}, {"role": "user", "content": text}],
                temperature=0, model=load_settings().llm_model)
        except Exception:
            return {"id": it["id"], "severity": "P2", "dimension": it["dimension"]}
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        return {"id": it["id"], "severity": "P2", "dimension": it["dimension"]}
    sev = d.get("severity", "P2")
    sev = sev if sev in SEVS else "P2"
    dim = d.get("dimension")
    new_dim = dim if (reassign and dim in DIMS and dim != "可复用沉淀") else it["dimension"]
    return {"id": it["id"], "severity": sev, "dimension": new_dim}


async def main():
    write = "--write" in sys.argv
    rows = [dict(r) for r in db.execute("SELECT * FROM checklist_items WHERE checklist_id=?", (CID,)).fetchall()]
    print(f"{'[写库]' if write else '[DRY-RUN]'} 清单#{CID}: {len(rows)}条，回填severity + 可复用沉淀重分配\n", flush=True)
    sem = asyncio.Semaphore(4)
    res = await asyncio.gather(*[judge(it, sem, it["dimension"] == "可复用沉淀") for it in rows])
    from collections import Counter
    print("severity分布:", dict(Counter(r["severity"] for r in res)))
    reassigned = [r for r, it in zip(res, rows) if it["dimension"] == "可复用沉淀"]
    print(f"可复用沉淀{len(reassigned)}条重分配到:", dict(Counter(r["dimension"] for r in reassigned)))
    if write:
        for r in res:
            db.execute("UPDATE checklist_items SET severity=?, dimension=? WHERE id=?",
                       (r["severity"], r["dimension"], r["id"]))
        db.commit()
        left = db.execute("SELECT COUNT(*) FROM checklist_items WHERE checklist_id=? AND dimension='可复用沉淀'", (CID,)).fetchone()[0]
        print(f"已更新{len(res)}条；可复用沉淀维度残留: {left}")


if __name__ == "__main__":
    asyncio.run(main())
