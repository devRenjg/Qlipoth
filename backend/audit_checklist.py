"""保障清单全面体检：按新判定标准(重大影响/资损无论起因都归"事故/故障"+P0)
重判每条的 dimension 与 severity。只改这两个字段，不动 现象/原因/处置/负责人/勾选/来源。

保守策略：以"纠正归错的事故"为主——逐条让 LLM 按新标准判，dry-run 打印变更，--write 应用。

用法: py -3.12 audit_checklist.py <checklist_id>          # dry-run
      py -3.12 audit_checklist.py <checklist_id> --write
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
CID = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 6
WRITE = "--write" in sys.argv
DIMS = ["事故/故障", "高可用保障", "直播播放体验", "安全", "业务需求", "成本"]
SEVS = ["P0", "P1", "P2"]

SYS = """你在按最新标准复核一条保障清单条目的【维度】和【严重度】。

六维度：事故/故障、高可用保障、直播播放体验、安全、业务需求、成本
【最高优先规则】只要这条**确实发生过**且造成了**重大影响或资损**(大面积影响用户/资金损失/严重故障)，
无论起因是攻击、bug、容量不足、还是依赖故障，都必须归到「事故/故障」维度，且 severity = P0。
不要因为"起因是安全攻击"就归到安全维度——安全维度只放"尚未酿成重大事故的防护措施/风险点"。

严重度：P0(重大事故/资损/大面积影响) / P1(重要需重点关注) / P2(一般改进项)。

返回严格 JSON(不要 markdown)：{"dimension":"事故/故障","severity":"P0","reason":"一句话"}
dimension 必须取自六维度之一，severity 取 P0/P1/P2。"""


async def judge(it, sem):
    text = (f"现象：{it['phenomenon']}\n原因：{it.get('cause','')}\n"
            f"当时处置：{it.get('handling','')}\n当前维度：{it['dimension']} 当前严重度：{it.get('severity')}")
    async with sem:
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": SYS}, {"role": "user", "content": text}],
                temperature=0, model=load_settings().llm_model)
        except Exception:
            return None
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
    except json.JSONDecodeError:
        return None
    dim = d.get("dimension")
    sev = d.get("severity")
    return {"dimension": dim if dim in DIMS else it["dimension"],
            "severity": sev if sev in SEVS else it.get("severity", "P2"),
            "reason": d.get("reason", "")}


async def main():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM checklist_items WHERE checklist_id=?", (CID,)).fetchall()]
    print(f"{'[写库]' if WRITE else '[DRY-RUN]'} 体检清单#{CID}：{len(rows)} 条\n", flush=True)
    sem = asyncio.Semaphore(4)
    done = {"n": 0}

    async def one(it):
        r = await judge(it, sem)
        done["n"] += 1
        if done["n"] % 40 == 0:
            print(f"  ...{done['n']}/{len(rows)}", flush=True)
        return it, r

    results = await asyncio.gather(*[one(it) for it in rows])
    failed = sum(1 for _it, r in results if r is None)
    if failed:
        print(f"\n⚠ 警告：{failed}/{len(rows)} 条 LLM 判定失败(返回空)——很可能是网关故障。", flush=True)
        if failed > len(rows) * 0.2:
            print("失败比例过高(>20%)，体检结果不可信，已中止。请待 LLM 恢复后重跑。", flush=True)
            db.close()
            return
    changes = []
    for it, r in results:
        if not r:
            continue
        if r["dimension"] != it["dimension"] or r["severity"] != (it.get("severity") or "P2"):
            changes.append((it, r))

    # 重点突出：升级为事故/故障 或 升级为P0 的
    to_incident = [(it, r) for it, r in changes if r["dimension"] == "事故/故障" and it["dimension"] != "事故/故障"]
    to_p0 = [(it, r) for it, r in changes if r["severity"] == "P0" and (it.get("severity") != "P0")]
    print(f"\n=== 变更 {len(changes)} 条 ===")
    print(f"  升级到「事故/故障」维度: {len(to_incident)} 条")
    print(f"  升级到 P0: {len(to_p0)} 条")
    print("\n升级到事故/故障 的条目:")
    for it, r in to_incident[:20]:
        print(f"  [{it['dimension']}/{it.get('severity')}→事故/{r['severity']}] {it['phenomenon'][:38]} | {r['reason'][:24]}")
    print("\n其他维度/级别变更(前15):")
    other = [(it, r) for it, r in changes if (it, r) not in to_incident]
    for it, r in other[:15]:
        print(f"  [{it['dimension']}/{it.get('severity')}→{r['dimension']}/{r['severity']}] {it['phenomenon'][:34]}")

    if WRITE:
        for it, r in changes:
            db.execute("UPDATE checklist_items SET dimension=?, severity=? WHERE id=?",
                       (r["dimension"], r["severity"], it["id"]))
        db.commit()
        from collections import Counter
        rows2 = db.execute("SELECT dimension,severity FROM checklist_items WHERE checklist_id=?", (CID,)).fetchall()
        print(f"\n已应用 {len(changes)} 条变更")
        print("体检后维度:", dict(Counter(r[0] for r in rows2)))
        print("体检后严重度:", dict(Counter(r[1] for r in rows2)))
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
