"""标签过滤搜索 质量×效率评测（用户点击标签后的搜索）。

模拟用户在前端勾选某个标签后再搜索的真实路径：检索结果先裁剪到该标签的文档
子集，再走选文件 + BM25 融合。对每道有标签的 golden 题，用其首个标签做过滤，
对比"无过滤"与"标签过滤"的 Recall/MRR/延迟，验证标签过滤的稳健性（不应掉召回，
且应更快或相当）。

复用生产路径：grep_search → _tagged_stored_paths → _filter_results_by_paths
→ _select_files → fuse_select（与 routes/query.py 的 tag_ids 分支一致）。

用法: py -3.12 eval/tag_filter_eval.py
输出: eval/reports/tagfilter_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import generate_search_strategy            # noqa: E402
from searcher import grep_search                     # noqa: E402
from routes.query import _select_files, _tagged_stored_paths, _filter_results_by_paths  # noqa: E402
from config import load_settings                     # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.json"
CACHE = Path(__file__).parent / "strategy_cache.json"
REPORTS = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))
K = 10


def _norm(n):
    return os.path.basename(n).strip()


def _tag_name_to_id():
    db = sqlite3.connect(load_settings().db_path)
    m = {r[1]: r[0] for r in db.execute("SELECT id, name FROM tags").fetchall()}
    db.close()
    return m


def _fuse(selected, question):
    try:
        from bm25 import fuse_select
        return [_norm(f) for f in fuse_select(selected, question, max_files=K)]
    except Exception:  # noqa: BLE001
        return selected


async def _eval_one(q, cache, tag2id):
    """对一题：无过滤 vs 标签过滤，各算命中与延迟。"""
    question = q["question"]
    relevant = {_norm(f) for f in q.get("relevant_files", [])}
    tags = q.get("tags", [])
    if not tags:
        return None
    tag = tags[0]
    tid = tag2id.get(tag)
    if tid is None:
        return None

    c = cache.get(str(q["id"]))
    if c and "error" not in c and c.get("question") == question:
        kws, fp = c["keywords"], c.get("file_pattern", "*")
    else:
        strat, _ = await generate_search_strategy(question)
        kws, fp = strat.get("keywords", [question]), strat.get("file_pattern", "*")

    # 无过滤
    t0 = time.perf_counter()
    results = grep_search(kws, fp)
    sel = _fuse([_norm(f) for f in _select_files(results, K)], question)
    lat_no = time.perf_counter() - t0

    # 标签过滤（模拟用户点了该标签）
    t0 = time.perf_counter()
    results2 = grep_search(kws, fp)
    allowed = await _tagged_stored_paths([tid])
    results2 = _filter_results_by_paths(results2, allowed)
    sel2 = _fuse([_norm(f) for f in _select_files(results2, K)], question)
    lat_tag = time.perf_counter() - t0

    def hit_rank(sel):
        h = len(relevant & set(sel))
        rank = next((i for i, f in enumerate(sel, 1) if f in relevant), None)
        return h, (1.0 / rank if rank else 0.0)

    h_no, rr_no = hit_rank(sel)
    h_tag, rr_tag = hit_rank(sel2)
    return {
        "id": q["id"], "type": q["type"], "tag": tag,
        "no_filter": {"hit": int(h_no > 0), "rr": rr_no, "lat": lat_no, "n_cand": len(results)},
        "tag_filter": {"hit": int(h_tag > 0), "rr": rr_tag, "lat": lat_tag, "n_cand": len(results2)},
    }


def _agg(rows, key):
    n = len(rows)
    if not n:
        return {}
    recall = sum(r[key]["hit"] for r in rows) / n
    mrr = sum(r[key]["rr"] for r in rows) / n
    lat = sum(r[key]["lat"] for r in rows) / n
    cand = sum(r[key]["n_cand"] for r in rows) / n
    return {"n": n, "recall": round(recall, 4), "mrr": round(mrr, 4),
            "lat_mean": round(lat, 3), "cand_mean": round(cand, 1)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    tag2id = _tag_name_to_id()
    print(f"标签过滤评测：{len(golden)} 题，按各题首标签模拟点击过滤", flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    async def _g(q):
        async with sem:
            try:
                return await _eval_one(q, cache, tag2id)
            except Exception as e:  # noqa: BLE001
                print(f"  #{q['id']} ERR {e}", flush=True)
                return None

    t0 = time.perf_counter()
    rows = [r for r in await asyncio.gather(*[_g(q) for q in golden]) if r]
    wall = time.perf_counter() - t0

    no = _agg(rows, "no_filter")
    tg = _agg(rows, "tag_filter")
    print(f"\n=== 标签过滤 vs 无过滤 ({len(rows)}题, wall={wall:.0f}s) ===")
    print(f"{'方案':<12}{'Recall':>9}{'MRR':>9}{'延迟均值':>10}{'候选均值':>10}")
    print(f"{'无过滤':<12}{no['recall']:>9}{no['mrr']:>9}{no['lat_mean']:>10}{no['cand_mean']:>10}")
    print(f"{'标签过滤':<12}{tg['recall']:>9}{tg['mrr']:>9}{tg['lat_mean']:>10}{tg['cand_mean']:>10}")

    # 按标签分组的过滤后表现
    by_tag = defaultdict(list)
    for r in rows:
        by_tag[r["tag"]].append(r)
    bt = {t: {"no": _agg(rs, "no_filter"), "tag": _agg(rs, "tag_filter")}
          for t, rs in by_tag.items()}

    REPORTS.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "n": len(rows), "wall_s": round(wall, 1),
           "no_filter": no, "tag_filter": tg, "by_tag": bt}
    (REPORTS / f"tagfilter_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS / f"tagfilter_{ts}.md", rep)
    print(f"\n报告: eval/reports/tagfilter_{ts}.md (+ .json)")


def _write_md(path, rep):
    no, tg = rep["no_filter"], rep["tag_filter"]
    lines = [f"# 标签过滤搜索 质量×效率 {rep['timestamp']}", "",
             f"- {rep['n']} 题，按各题首标签模拟「用户点击标签后搜索」，对比无过滤",
             "- 路径：grep → tag 裁剪 → 选文件 → BM25 融合（与生产 tag_ids 分支一致）",
             "", "## 总体", "",
             "| 方案 | Recall@10 | MRR | 延迟均值(s) | 候选数均值 |",
             "|------|-----------|-----|-------------|------------|",
             f"| 无过滤 | {no['recall']} | {no['mrr']} | {no['lat_mean']} | {no['cand_mean']} |",
             f"| 标签过滤 | {tg['recall']} | {tg['mrr']} | {tg['lat_mean']} | {tg['cand_mean']} |",
             "", "## 分标签（标签过滤后）", "",
             "| 标签 | 题数 | 无过滤Recall | 过滤后Recall | 无过滤延迟 | 过滤后延迟 |",
             "|------|------|-------------|-------------|-----------|-----------|"]
    for t, d in sorted(rep["by_tag"].items(), key=lambda x: -x[1]["tag"]["n"]):
        lines.append(f"| {t} | {d['tag']['n']} | {d['no']['recall']} | {d['tag']['recall']} | "
                     f"{d['no']['lat_mean']} | {d['tag']['lat_mean']} |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
