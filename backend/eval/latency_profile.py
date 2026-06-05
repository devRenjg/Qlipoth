"""搜索延迟 Profiler（Task8）：基于 golden set 量化端到端各阶段真实耗时。

定位"分钟级慢"卡在哪一环。复用生产链路真实函数（generate_search_strategy /
grep_search / _select_files / fuse_select / generate_answer），逐阶段计时：

  strategy_llm : LLM 生成搜索策略（阻塞，回答前）
  grep         : ripgrep 全库搜索
  select+fuse  : IDF 选文件 + BM25/RRF 旁路融合
  answer_llm   : LLM 生成最终回答（大头，受 context 长度影响）
  answer_ttfb  : 流式首字延迟（用户感知的"开始出字"时间）

用法:
    py -3.12 eval/latency_profile.py --n 12         # 采样12题
    py -3.12 eval/latency_profile.py --n 12 --stream  # 额外测流式TTFB
输出: eval/reports/latency_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import generate_search_strategy, generate_answer, stream_answer  # noqa: E402
from searcher import grep_search                                          # noqa: E402
from routes.query import _select_files, _assemble_context                 # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))
    return s[i]


async def _profile_one(q, stream=False):
    """对一题分解计时。返回各阶段秒数 + context 字数。"""
    question = q["question"]
    r = {"id": q["id"], "type": q.get("type", ""), "question": question}

    t0 = time.perf_counter()
    strategy, strat_llm = await generate_search_strategy(question)
    r["strategy_total"] = time.perf_counter() - t0
    r["strategy_llm"] = strat_llm
    keywords = strategy.get("keywords", [question])
    file_pattern = strategy.get("file_pattern", "*")

    t0 = time.perf_counter()
    results = grep_search(keywords, file_pattern)
    r["grep"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    search_text, files_to_read, _ = _assemble_context(results, question) if results else ("未找到", [], [])
    r["select_fuse_assemble"] = time.perf_counter() - t0
    r["context_chars"] = len(search_text)
    r["n_files"] = len(files_to_read)

    if stream:
        t0 = time.perf_counter()
        ttfb = None
        chars = 0
        async for chunk in stream_answer(question, search_text):
            if ttfb is None:
                ttfb = time.perf_counter() - t0
            chars += len(chunk)
        r["answer_ttfb"] = ttfb or 0.0
        r["answer_total"] = time.perf_counter() - t0
        r["answer_chars"] = chars
    else:
        t0 = time.perf_counter()
        answer, ans_llm = await generate_answer(question, search_text)
        r["answer_total"] = time.perf_counter() - t0
        r["answer_llm"] = ans_llm
        r["answer_chars"] = len(answer)

    r["end_to_end"] = (r["strategy_total"] + r["grep"] +
                       r["select_fuse_assemble"] + r["answer_total"])
    return r


def _sample(golden, n):
    """按 type 分层采样 n 题，保证类型覆盖。"""
    from collections import defaultdict
    by_type = defaultdict(list)
    for q in golden:
        by_type[q.get("type", "")].append(q)
    out = []
    types = list(by_type)
    i = 0
    while len(out) < n and any(by_type.values()):
        t = types[i % len(types)]
        if by_type[t]:
            out.append(by_type[t].pop(0))
        i += 1
    return out[:n]


def _agg(rows, key):
    vals = [r[key] for r in rows if key in r]
    if not vals:
        return {"mean": 0, "p50": 0, "p90": 0, "max": 0}
    return {"mean": round(sum(vals) / len(vals), 2), "p50": round(_pct(vals, 50), 2),
            "p90": round(_pct(vals, 90), 2), "max": round(max(vals), 2)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--stream", action="store_true")
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    sample = _sample(golden, args.n)
    print(f"Profile {len(sample)} 题（分层采样） stream={args.stream} concurrency={args.concurrency}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    async def _one(q):
        async with sem:
            try:
                r = await _profile_one(q, args.stream)
                print(f"  #{r['id']:<3}[{r['type']:<5}] e2e={r['end_to_end']:5.1f}s  "
                      f"strat={r['strategy_llm']:.1f} grep={r['grep']:.2f} "
                      f"fuse={r['select_fuse_assemble']:.2f} ans={r['answer_total']:.1f} "
                      f"ctx={r['context_chars']}", flush=True)
                return r
            except Exception as e:  # noqa: BLE001
                print(f"  #{q['id']} ERROR: {e}", flush=True)
                return {"id": q["id"], "error": str(e)}

    t_start = time.perf_counter()
    rows = await asyncio.gather(*[_one(q) for q in sample])
    wall = time.perf_counter() - t_start
    rows = [r for r in rows if "error" not in r]

    stage_keys = ["strategy_llm", "grep", "select_fuse_assemble", "answer_total", "end_to_end"]
    if args.stream:
        stage_keys.insert(3, "answer_ttfb")
    aggs = {k: _agg(rows, k) for k in stage_keys}
    ctx = _agg(rows, "context_chars")

    print(f"\n=== 阶段耗时聚合 ({len(rows)}题, wall={wall:.0f}s) ===")
    print(f"{'阶段':<22}{'mean':>8}{'p50':>8}{'p90':>8}{'max':>8}")
    for k in stage_keys:
        a = aggs[k]
        print(f"{k:<22}{a['mean']:>8}{a['p50']:>8}{a['p90']:>8}{a['max']:>8}")
    e2e = aggs["end_to_end"]["mean"]
    share = round((aggs['strategy_llm']['mean'] + aggs['answer_total']['mean']) / e2e * 100) if e2e else 0
    print(f"\n端到端均值 {e2e}s；LLM 两段占 {share}%；context: mean={ctx['mean']} p90={ctx['p90']}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"), "n": len(rows),
           "stream": args.stream, "wall_s": round(wall, 1), "model": "claude-opus-4-8",
           "aggs": aggs, "context_chars": ctx, "rows": rows}
    (REPORTS_DIR / f"latency_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"latency_{ts}.md", rep)
    print(f"\n报告: eval/reports/latency_{ts}.md (+ .json)")


def _write_md(path, rep):
    a = rep["aggs"]
    lines = [
        f"# 搜索延迟 Profile {rep['timestamp']}",
        "",
        f"- 采样 {rep['n']} 题（分层） 模型 {rep['model']} stream={rep['stream']}",
        f"- context 字数: mean={rep['context_chars']['mean']} / p90={rep['context_chars']['p90']} / max={rep['context_chars']['max']}",
        "",
        "## 阶段耗时（秒）",
        "",
        "| 阶段 | mean | p50 | p90 | max |",
        "|------|------|-----|-----|-----|",
    ]
    for k, v in a.items():
        lines.append(f"| {k} | {v['mean']} | {v['p50']} | {v['p90']} | {v['max']} |")
    e2e = a["end_to_end"]["mean"]
    share = round((a["strategy_llm"]["mean"] + a["answer_total"]["mean"]) / e2e * 100) if e2e else 0
    lines += ["", f"**端到端均值 {e2e}s，LLM 两段（策略+回答）占 ~{share}%。**", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
