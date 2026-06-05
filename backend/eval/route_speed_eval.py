"""端到端路由横评（方案2 第二步）：按规则分类路由，对比全opus/全sonnet。

基于第一步结论：规则分类 5类85%/二分90%、零延迟，故路由用规则分类。
基于上轮发现：sonnet 在负责人类弱(4.12 vs opus4.57)、其余类够用。

路由策略（router）：
  负责人类 + 方案类 + 歧义类 → opus（质量敏感，sonnet 弱或复杂分析）
  数量类 + 排查类           → sonnet（上轮横评质量已够用）
  context：所有方案统一走现行检索链路（策略缓存→grep→fuse），公平对比仅变回答路径

对照：
  all_opus   : 全 opus（线上现状）
  all_sonnet : 全 sonnet（方案B）
  router     : 规则分类路由（本方案2）

固定检索上下文，质量 opus 裁判 4 维均分。

用法: py -3.12 eval/route_speed_eval.py --n 0   # 0=全部100题
输出: eval/reports/route_speed_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import llm                                       # noqa: E402
from searcher import grep_search                 # noqa: E402
from routes.query import _assemble_context       # noqa: E402
from eval.sim_personas import _avg_score         # noqa: E402
from eval.answer_speed_eval import _answer_with_model, _judge  # noqa: E402
from eval.classifier_eval import classify_rule   # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
CACHE_PATH = Path(__file__).parent / "strategy_cache.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))

OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-4-6"
# 路由表：分类→回答模型
ROUTE = {"负责人类": OPUS, "方案类": OPUS, "歧义类": OPUS,
         "数量类": SONNET, "排查类": SONNET}
SHORT = {OPUS: "opus", SONNET: "sonnet"}
STRATS = ["all_opus", "all_sonnet", "router"]


def _sample(golden, n):
    by = defaultdict(list)
    for q in golden:
        by[q.get("type", "")].append(q)
    out, types, i = [], list(by), 0
    while len(out) < n and any(by.values()):
        t = types[i % len(types)]
        if by[t]:
            out.append(by[t].pop(0))
        i += 1
    return out[:n]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="0=全部100题")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    sample = golden if args.n == 0 else _sample(golden, args.n)
    print(f"端到端路由横评 {len(sample)} 题 × {len(STRATS)} 策略", flush=True)

    items = []
    for q in sample:
        c = cache.get(str(q["id"]))
        if not c or "error" in c:
            continue
        results = grep_search(c["keywords"], c.get("file_pattern", "*"))
        search_text, _, _ = _assemble_context(results, q["question"]) if results else ("未找到", [], [])
        items.append({"q": q, "prompt": llm.ANSWER_PROMPT.format(
            soul=llm.SOUL, search_results=search_text, question=q["question"]),
            "pred": classify_rule(q["question"])})
    print(f"  {len(items)} 题就绪", flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    def model_for(strat, pred):
        if strat == "all_opus":
            return OPUS
        if strat == "all_sonnet":
            return SONNET
        return ROUTE.get(pred, SONNET)

    async def _gen(item, strat):
        async with sem:
            q = item["q"]
            model = model_for(strat, item["pred"])
            try:
                ans, ttfb, total = await _answer_with_model(item["prompt"], model)
                verdict = await _judge(q["question"], ans)
                avg = _avg_score(verdict) if "error" not in verdict else 0.0
                return {"id": q["id"], "type": q["type"], "pred": item["pred"],
                        "strat": strat, "model": SHORT[model], "ttfb": ttfb,
                        "total": total, "avg": avg}
            except Exception as e:  # noqa: BLE001
                return {"id": q["id"], "strat": strat, "error": str(e)}

    tasks = [_gen(it, s) for it in items for s in STRATS]
    t0 = time.perf_counter()
    rows = await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0

    def agg(strat):
        rs = [r for r in rows if r["strat"] == strat and "error" not in r]
        if not rs:
            return {"n": 0}
        ttfb = [r["ttfb"] for r in rs]
        total = [r["total"] for r in rs]
        qual = [r["avg"] for r in rs if r["avg"] > 0]
        n_opus = sum(1 for r in rs if r["model"] == "opus")
        return {"n": len(rs), "ttfb_mean": round(sum(ttfb) / len(ttfb), 2),
                "total_mean": round(sum(total) / len(total), 2),
                "total_p90": round(sorted(total)[min(len(total)-1, int(0.9*(len(total)-1)))], 2),
                "quality_mean": round(sum(qual) / len(qual), 2) if qual else 0,
                "opus_share": round(n_opus / len(rs), 2)}

    aggs = {s: agg(s) for s in STRATS}
    print(f"\n=== 端到端路由横评 ({len(items)}题, wall={wall:.0f}s) ===")
    print(f"{'策略':<12}{'TTFB':>8}{'Total':>8}{'p90':>8}{'质量':>8}{'opus占比':>10}")
    for s in STRATS:
        a = aggs[s]
        print(f"{s:<12}{a.get('ttfb_mean',0):>8}{a.get('total_mean',0):>8}"
              f"{a.get('total_p90',0):>8}{a.get('quality_mean',0):>8}{a.get('opus_share',0):>10}")

    by_type = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if "error" in r or r["avg"] <= 0:
            continue
        by_type[r["type"]][r["strat"]].append(r["avg"])
    types = ["数量类", "负责人类", "排查类", "方案类", "歧义类"]
    type_q = {}
    print("\n=== 分类型质量(均分) ===")
    print(f"{'类型':<8}{'all_opus':>10}{'all_sonnet':>12}{'router':>9}")
    for t in types:
        d = by_type.get(t, {})
        def m(s):
            v = d.get(s, [])
            return round(sum(v) / len(v), 2) if v else 0
        type_q[t] = {"all_opus": m("all_opus"), "all_sonnet": m("all_sonnet"), "router": m("router")}
        print(f"{t:<8}{type_q[t]['all_opus']:>10}{type_q[t]['all_sonnet']:>12}{type_q[t]['router']:>9}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "n": len(items), "wall_s": round(wall, 1), "strats": STRATS,
           "route_table": {k: SHORT[v] for k, v in ROUTE.items()},
           "aggs": aggs, "type_quality": type_q, "rows": rows}
    (REPORTS_DIR / f"route_speed_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"route_speed_{ts}.md", rep)
    print(f"\n报告: eval/reports/route_speed_{ts}.md (+ .json)")


def _write_md(path, rep):
    lines = [f"# 端到端路由横评（方案2） {rep['timestamp']}", "",
             f"- {rep['n']} 题，固定检索上下文，仅变回答路径；质量 opus 裁判均分(0-5)",
             f"- 路由表（规则分类→模型）：{rep['route_table']}",
             "", "## 总体：耗时 × 质量", "",
             "| 策略 | TTFB(s) | Total(s) | Total p90(s) | 质量(0-5) | opus占比 |",
             "|------|---------|----------|--------------|-----------|----------|"]
    for s in rep["strats"]:
        a = rep["aggs"][s]
        lines.append(f"| {s} | {a.get('ttfb_mean',0)} | {a.get('total_mean',0)} | "
                     f"{a.get('total_p90',0)} | {a.get('quality_mean',0)} | {a.get('opus_share',0)} |")
    lines += ["", "## 分类型质量（验证 router 是否靠 opus 守住负责人类）", "",
              "| 类型 | all_opus | all_sonnet | router |",
              "|------|----------|------------|--------|"]
    for t, d in rep["type_quality"].items():
        lines.append(f"| {t} | {d['all_opus']} | {d['all_sonnet']} | {d['router']} |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
