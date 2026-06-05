"""端到端组合提速横评（Task8）：把检索段+回答段两个杠杆合一，对比线上现状。

决定性对比：完整 end-to-end（问完→拿到完整回答）的延迟 × 质量。

配置：
  prod         : LLM策略→grep→fuse→opus回答（线上现状）
  fast_sonnet  : BM25直召（零LLM检索）→sonnet回答
  fast_haiku   : BM25直召（零LLM检索）→haiku回答
  balanced     : BM25直召→opus回答（只砍检索段LLM，回答仍opus，质量上限）

检索质量用 golden set（Recall@10/全命中），回答质量用 opus 裁判 4 维均分。

用法: py -3.12 eval/e2e_speed_eval.py --n 15
输出: eval/reports/e2e_speed_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import os
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import llm                                       # noqa: E402
from searcher import grep_search                 # noqa: E402
from routes.query import _assemble_context, _select_files  # noqa: E402
from config import load_settings                 # noqa: E402
from bm25 import _get_index                       # noqa: E402
from eval.sim_personas import JUDGE_SYSTEM, _parse_json, _avg_score  # noqa: E402
from eval.answer_speed_eval import _answer_with_model, _judge        # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
CACHE_PATH = Path(__file__).parent / "strategy_cache.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))

OPUS = "claude-opus-4-8"
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"

CONFIGS = {
    "prod":        {"retr": "llm",  "model": OPUS,   "desc": "LLM策略+opus(线上现状)"},
    "balanced":    {"retr": "bm25", "model": OPUS,   "desc": "BM25直召+opus"},
    "fast_sonnet": {"retr": "bm25", "model": SONNET, "desc": "BM25直召+sonnet"},
    "fast_haiku":  {"retr": "bm25", "model": HAIKU,  "desc": "BM25直召+haiku"},
}


def _norm(name):
    return os.path.basename(name).strip()


def _sample(golden, n):
    from collections import defaultdict
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


async def _retrieve(q, mode, cache):
    """返回 (files, search_text, retr_latency_s)。mode: 'llm' 现行 / 'bm25' 纯BM25直召。"""
    question = q["question"]
    idx = _get_index()
    t0 = time.perf_counter()
    if mode == "llm":
        c = cache.get(str(q["id"]))
        if c and "error" not in c:
            kws, fp = c["keywords"], c.get("file_pattern", "*")
            retr_extra = 0.0  # 缓存：策略 LLM 时间另算（线上真实约 6.5s）
        else:
            strat, _ = await llm.generate_search_strategy(question)
            kws, fp = strat.get("keywords", [question]), strat.get("file_pattern", "*")
            retr_extra = 0.0
        results = grep_search(kws, fp)
        search_text, files, _ = _assemble_context(results, question) if results else ("未找到", [], [])
        # 线上现状策略 LLM 真实耗时（profile 实测均值），缓存模式补回
        lat = time.perf_counter() - t0 + (6.5 if (c and "error" not in c) else 0.0)
    else:  # bm25 直召
        bm_files = idx.topk(question, 10) if idx else []
        # 复用 _assemble_context 需要 grep results 才能摘录；BM25直召用整篇读入
        from searcher import read_file_content
        parts, total = [], 0
        files = [_norm(f) for f in bm_files]
        for f in bm_files[:10]:
            content = read_file_content(f)
            if not content:
                continue
            budget = 60000 // min(len(bm_files), 10)
            chunk = f"=== 文件: {f} ===\n{content[:budget]}\n"
            if total + len(chunk) > 60000:
                break
            parts.append(chunk)
            total += len(chunk)
        search_text = "\n".join(parts) or "未找到相关内容。"
        lat = time.perf_counter() - t0
    return files, search_text, lat


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    sample = _sample(golden, args.n)
    print(f"端到端组合横评 {len(sample)} 题 × {len(CONFIGS)} 配置", flush=True)
    _get_index()

    sem = asyncio.Semaphore(args.concurrency)

    async def _run(q, cfg_name):
        cfg = CONFIGS[cfg_name]
        async with sem:
            rel = set(_norm(f) for f in q.get("relevant_files", []))
            try:
                files, search_text, retr_lat = await _retrieve(q, cfg["retr"], cache)
                prompt = llm.ANSWER_PROMPT.format(soul=llm.SOUL, search_results=search_text,
                                                  question=q["question"])
                ans, ttfb, ans_total = await _answer_with_model(prompt, cfg["model"])
                verdict = await _judge(q["question"], ans)
                avg = _avg_score(verdict) if "error" not in verdict else 0.0
                hit = 1.0 if (rel & set(files)) else 0.0
                e2e_ttfb = retr_lat + ttfb
                e2e_total = retr_lat + ans_total
                print(f"  #{q['id']:<3} {cfg_name:<12} e2e首字={e2e_ttfb:5.1f}s 完整={e2e_total:5.1f}s "
                      f"检索={retr_lat:4.1f} 命中={int(hit)} 质量={avg:.1f}", flush=True)
                return {"id": q["id"], "type": q.get("type", ""), "cfg": cfg_name,
                        "retr_lat": retr_lat, "e2e_ttfb": e2e_ttfb, "e2e_total": e2e_total,
                        "hit": hit, "quality": avg}
            except Exception as e:  # noqa: BLE001
                print(f"  #{q['id']} {cfg_name} ERROR: {e}", flush=True)
                return {"id": q["id"], "cfg": cfg_name, "error": str(e)}

    tasks = [_run(q, c) for q in sample for c in CONFIGS]
    t0 = time.perf_counter()
    rows = await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0

    def agg(cfg_name):
        rs = [r for r in rows if r["cfg"] == cfg_name and "error" not in r]
        if not rs:
            return {"n": 0}
        ttfb = [r["e2e_ttfb"] for r in rs]
        total = [r["e2e_total"] for r in rs]
        qual = [r["quality"] for r in rs if r["quality"] > 0]
        return {"n": len(rs),
                "e2e_ttfb_mean": round(sum(ttfb) / len(ttfb), 1),
                "e2e_total_mean": round(sum(total) / len(total), 1),
                "e2e_total_p90": round(sorted(total)[min(len(total)-1, int(0.9*(len(total)-1)))], 1),
                "retr_mean": round(sum(r["retr_lat"] for r in rs) / len(rs), 1),
                "hit_rate": round(sum(r["hit"] for r in rs) / len(rs), 3),
                "quality_mean": round(sum(qual) / len(qual), 2) if qual else 0}

    aggs = {c: agg(c) for c in CONFIGS}
    print(f"\n=== 端到端组合横评 ({len(sample)}题, wall={wall:.0f}s) ===")
    print(f"{'配置':<13}{'首字':>8}{'完整':>8}{'p90':>8}{'检索':>8}{'命中率':>9}{'质量':>8}")
    for c in CONFIGS:
        a = aggs[c]
        print(f"{c:<13}{a.get('e2e_ttfb_mean',0):>8}{a.get('e2e_total_mean',0):>8}"
              f"{a.get('e2e_total_p90',0):>8}{a.get('retr_mean',0):>8}"
              f"{a.get('hit_rate',0):>9}{a.get('quality_mean',0):>8}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "n": len(sample), "wall_s": round(wall, 1),
           "configs": {c: CONFIGS[c]["desc"] for c in CONFIGS}, "aggs": aggs, "rows": rows}
    (REPORTS_DIR / f"e2e_speed_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"e2e_speed_{ts}.md", rep)
    print(f"\n报告: eval/reports/e2e_speed_{ts}.md (+ .json)")


def _write_md(path, rep):
    lines = [f"# 端到端组合提速横评 {rep['timestamp']}", "",
             f"- {rep['n']} 题；首字/完整为 end-to-end（检索+回答）；命中率=检索top10含GT；质量=opus裁判均分0-5",
             "- prod 检索延迟含策略 LLM 实测均值 6.5s（缓存模式补回，反映线上真实）",
             "", "| 配置 | 说明 | e2e首字(s) | e2e完整(s) | 完整p90(s) | 检索(s) | 命中率 | 质量(0-5) |",
             "|------|------|-----------|-----------|-----------|---------|--------|-----------|"]
    for c in rep["configs"]:
        a = rep["aggs"][c]
        lines.append(f"| {c} | {rep['configs'][c]} | {a.get('e2e_ttfb_mean',0)} | "
                     f"{a.get('e2e_total_mean',0)} | {a.get('e2e_total_p90',0)} | {a.get('retr_mean',0)} | "
                     f"{a.get('hit_rate',0)} | {a.get('quality_mean',0)} |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
