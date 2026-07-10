"""直播日历问答评测：校验「路由准确率 + 数值准确率」。

两个维度（都不依赖 LLM，离线跑，秒级出结果）：
  1) 路由准确率 route_acc：detect_livecal_intent + fetch_livecal_context 决定
     该问题走 live_calendar 还是 knowledge_base，与 expected_source 是否一致。
       - 命中日历意图且能取到数据 → live_calendar
       - 未命中意图 / 取不到数据（如 S17 未来赛事）→ knowledge_base（回退）
  2) 数值准确率 value_acc：对 expected_source=live_calendar 的题，
     检查 fetch_livecal_context 返回文本是否包含 expected_contains 中任一（OR 命中即算对）。
     expected_contains 为空的边界题不计入数值分母。

为什么这样设计：路由是「优先用日历精准数据」这条策略的开关，错了后面全错；
数值是「取回来的数据对不对」。两者分开算，能定位是路由问题还是取数问题。

用法:
    py -3.12 eval/livecal_eval.py
输出: eval/reports/livecal_<时间戳>.md (+ .json)
"""
import asyncio
import io
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from livecal_qa import detect_livecal_intent, fetch_livecal_context  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_livecal.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))


async def decide_source(q: str):
    """复刻 routes/query.py 的路由判定：返回 (source, ctx)。"""
    if not detect_livecal_intent(q):
        return "knowledge_base", None
    ctx = await fetch_livecal_context(q)
    if ctx is None:
        return "knowledge_base", None  # 命中意图但无数据 → 回退
    return "live_calendar", ctx


async def run():
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]
    rows = []
    route_ok = 0
    val_ok = 0
    val_total = 0

    for c in cases:
        exp_src = c["expected_source"]
        src, ctx = await decide_source(c["question"])
        r_hit = src == exp_src
        route_ok += r_hit

        v_hit = None
        contains = c.get("expected_contains") or []
        if exp_src == "live_calendar" and contains:
            val_total += 1
            text = ctx or ""
            v_hit = any(str(t) in text for t in contains)
            val_ok += v_hit

        rows.append({
            "id": c["id"], "question": c["question"],
            "expected_source": exp_src, "got_source": src,
            "route_ok": r_hit, "value_ok": v_hit,
            "note": c.get("note", ""),
        })

    n = len(cases)
    route_acc = route_ok / n if n else 0.0
    value_acc = val_ok / val_total if val_total else 0.0

    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    REPORTS_DIR.mkdir(exist_ok=True)

    # 控制台摘要
    print(f"直播日历问答评测  共 {n} 题")
    print(f"  路由准确率 route_acc : {route_acc:.1%}  ({route_ok}/{n})")
    print(f"  数值准确率 value_acc : {value_acc:.1%}  ({val_ok}/{val_total})")
    fails = [r for r in rows if not r["route_ok"] or r["value_ok"] is False]
    if fails:
        print(f"\n未通过 {len(fails)} 题：")
        for r in fails:
            tag = []
            if not r["route_ok"]:
                tag.append(f"路由:期望{r['expected_source']}→实际{r['got_source']}")
            if r["value_ok"] is False:
                tag.append("数值:未命中")
            print(f"  [{r['id']}] {r['question']}  ({'; '.join(tag)})")
    else:
        print("\n✅ 全部通过")

    # 落报告
    md = [f"# 直播日历问答评测 {ts}", "",
          f"- 题目数: {n}",
          f"- 路由准确率: **{route_acc:.1%}** ({route_ok}/{n})",
          f"- 数值准确率: **{value_acc:.1%}** ({val_ok}/{val_total})", "",
          "| id | 问题 | 期望 | 实际 | 路由 | 数值 | 备注 |",
          "|----|------|------|------|------|------|------|"]
    for r in rows:
        v = {True: "✓", False: "✗", None: "—"}[r["value_ok"]]
        rk = "✓" if r["route_ok"] else "✗"
        md.append(f"| {r['id']} | {r['question']} | {r['expected_source']} | "
                  f"{r['got_source']} | {rk} | {v} | {r['note']} |")
    (REPORTS_DIR / f"livecal_{ts}.md").write_text("\n".join(md), encoding="utf-8")
    (REPORTS_DIR / f"livecal_{ts}.json").write_text(
        json.dumps({"route_acc": route_acc, "value_acc": value_acc,
                    "route_ok": route_ok, "value_ok": val_ok,
                    "value_total": val_total, "n": n, "rows": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告: eval/reports/livecal_{ts}.md")
    return route_acc, value_acc


if __name__ == "__main__":
    asyncio.run(run())
