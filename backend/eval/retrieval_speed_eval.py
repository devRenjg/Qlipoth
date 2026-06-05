"""检索前端提速横评（Task8）：对比"产生 files_to_read 的检索段"各方案的质量与延迟。

瓶颈 profile 显示：策略生成 LLM 占 6.5s。本脚本横比能否去掉/替换这一段，
同时保持检索质量（Recall@10/MRR，复用 golden set 与 strategy_cache）。

方案：
  prod_cached  : 现行链路（LLM策略→grep→选文件→BM25融合），策略走缓存计时不含LLM
  prod_llm     : 同上，但策略实时 LLM（含真实 6.5s，反映线上）
  bm25_direct  : 纯 BM25 topk 从问题直接召回，零 LLM
  bm25_grep_rule: 规则分词关键词→grep→选文件→BM25融合，零 LLM（去 LLM 的现行结构）

质量口径与 ab_eval / paradigm_eval 一致（Recall@10=全命中率，MRR）。
延迟只计检索段（不含回答 LLM），即"用户问完到拿到 context"的时间。

用法: py -3.12 eval/retrieval_speed_eval.py
输出: eval/reports/retr_speed_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import os
import re
import time
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import generate_search_strategy   # noqa: E402
from searcher import grep_search           # noqa: E402
from routes.query import _select_files     # noqa: E402
from config import load_settings           # noqa: E402
from bm25 import _get_index, tokenize as bm25_tok, _rrf  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
CACHE_PATH = Path(__file__).parent / "strategy_cache.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))
K = 10
MODES = ["prod_cached", "prod_llm", "bm25_direct", "bm25_grep_rule"]

_STOP = set("的了吗呢啊呀吧么怎么这个那个有没有是不是要不要呗咱们我们你们他们".split())
_WORD_RE = re.compile(r'[a-zA-Z0-9]+|[一-鿿]{2,}')


def _norm(name):
    return os.path.basename(name).strip()


def _rule_keywords(question):
    """零 LLM 规则分词：抽英文/数字整词 + 中文 2gram+ 片段，去停用词，作为 grep 关键词。"""
    cands = _WORD_RE.findall(question)
    kws, seen = [], set()
    for c in cands:
        if c in _STOP or c in seen:
            continue
        seen.add(c)
        kws.append(c)
    return kws[:6] or [question]


def _fuse(baseline, question, idx):
    """复现生产 fuse_select：baseline ⊕ BM25 topk 的 RRF。"""
    if idx is None or not idx.n:
        return baseline
    bm = [_norm(f) for f in idx.topk(question, K)]
    if not bm:
        return baseline
    return [_norm(f) for f in _rrf([baseline, bm])][:K]


async def _retrieve(q, cache):
    """对一题跑各方案，返回 {mode: (files, latency_s)}。"""
    question = q["question"]
    out = {}
    idx = _get_index()

    c = cache.get(str(q["id"]))
    if c and "error" not in c:
        t0 = time.perf_counter()
        results = grep_search(c["keywords"], c.get("file_pattern", "*"))
        baseline = [_norm(f) for f in _select_files(results, K)]
        out["prod_cached"] = (_fuse(baseline, question, idx), time.perf_counter() - t0)

    t0 = time.perf_counter()
    try:
        strat, _ = await generate_search_strategy(question)
        results = grep_search(strat.get("keywords", [question]), strat.get("file_pattern", "*"))
        baseline = [_norm(f) for f in _select_files(results, K)]
        out["prod_llm"] = (_fuse(baseline, question, idx), time.perf_counter() - t0)
    except Exception:  # noqa: BLE001
        out["prod_llm"] = ([], time.perf_counter() - t0)

    t0 = time.perf_counter()
    bm = [_norm(f) for f in (idx.topk(question, K) if idx else [])]
    out["bm25_direct"] = (bm, time.perf_counter() - t0)

    t0 = time.perf_counter()
    results = grep_search(_rule_keywords(question), "*")
    baseline = [_norm(f) for f in _select_files(results, K)]
    out["bm25_grep_rule"] = (_fuse(baseline, question, idx), time.perf_counter() - t0)
    return out


def _metrics(rows, mode):
    recalls, rr, full, lat, n = [], [], 0, [], 0
    for r in rows:
        rel = r["relevant"]
        if not rel or mode not in r["res"]:
            continue
        files, latency = r["res"][mode]
        inter = len(rel & set(files))
        recalls.append(inter / len(rel))
        rank = next((i for i, f in enumerate(files, 1) if f in rel), None)
        rr.append(1.0 / rank if rank else 0.0)
        if inter == len(rel):
            full += 1
        lat.append(latency)
        n += 1
    if not n:
        return {"n": 0}
    return {"n": n, "recall": round(sum(recalls) / n, 4), "mrr": round(sum(rr) / n, 4),
            "full_hit": round(full / n, 4), "lat_mean": round(sum(lat) / n, 3),
            "lat_p90": round(sorted(lat)[min(n - 1, int(0.9 * (n - 1)))], 3)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="0=全部")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if args.n:
        golden = golden[:args.n]
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    print(f"检索段提速横评 {len(golden)} 题  @K={K}", flush=True)
    _get_index()

    sem = asyncio.Semaphore(args.concurrency)

    async def _one(q):
        async with sem:
            rel = set(_norm(f) for f in q.get("relevant_files", []))
            try:
                res = await _retrieve(q, cache)
            except Exception as e:  # noqa: BLE001
                return {"id": q["id"], "relevant": rel, "res": {}, "error": str(e)}
            return {"id": q["id"], "type": q.get("type", ""), "relevant": rel, "res": res}

    t0 = time.perf_counter()
    rows = await asyncio.gather(*[_one(q) for q in golden])
    wall = time.perf_counter() - t0

    overall = {m: _metrics(rows, m) for m in MODES}
    llm_flag = {"prod_cached": "缓存", "prod_llm": "有", "bm25_direct": "无", "bm25_grep_rule": "无"}
    print(f"\n=== 检索段质量×延迟 ({len(golden)}题, wall={wall:.0f}s) ===")
    print(f"{'方案':<16}{'Recall':>9}{'MRR':>9}{'Full':>8}{'lat_mean':>10}{'lat_p90':>10}{'LLM':>6}")
    for m in MODES:
        o = overall[m]
        print(f"{m:<16}{o.get('recall',0):>9}{o.get('mrr',0):>9}{o.get('full_hit',0):>8}"
              f"{o.get('lat_mean',0):>10}{o.get('lat_p90',0):>10}{llm_flag[m]:>6}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "n": len(golden), "k": K, "wall_s": round(wall, 1), "overall": overall}
    (REPORTS_DIR / f"retr_speed_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"retr_speed_{ts}.md", rep, llm_flag)
    print(f"\n报告: eval/reports/retr_speed_{ts}.md (+ .json)")


def _write_md(path, rep, llm_flag):
    lines = [f"# 检索段提速横评 {rep['timestamp']}", "",
             f"- {rep['n']} 题 @K={rep['k']}；延迟只计检索段（问完→拿到 context），不含回答 LLM",
             "", "| 方案 | Recall@10 | MRR | 全命中 | 延迟均值(s) | 延迟p90(s) | 策略LLM |",
             "|------|-----------|-----|--------|-------------|------------|---------|"]
    desc = {"prod_cached": "现行链路(策略走缓存)", "prod_llm": "现行链路(策略实时LLM=线上)",
            "bm25_direct": "纯BM25直召", "bm25_grep_rule": "规则关键词grep+BM25融合"}
    for m in ["prod_llm", "prod_cached", "bm25_direct", "bm25_grep_rule"]:
        o = rep["overall"][m]
        lines.append(f"| {desc[m]} | {o.get('recall',0)} | {o.get('mrr',0)} | "
                     f"{o.get('full_hit',0)} | {o.get('lat_mean',0)} | {o.get('lat_p90',0)} | {llm_flag[m]} |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
