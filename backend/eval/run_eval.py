"""评测运行器:对 golden_set.json 跑生产检索链路,输出 Recall@K / MRR / Precision@K。

复用生产代码(searcher.grep_search + query._select_files),保证评测的就是线上行为。

用法:
    py -3.12 eval/run_eval.py                      # 跑 golden_set.json
    py -3.12 eval/run_eval.py --golden xxx.json    # 指定评测集
    py -3.12 eval/run_eval.py --k 10 --tag baseline # 报告打标签

输出:
    - 终端打印总体 + 分类型指标
    - eval/reports/report_<时间戳>_<tag>.md (可 git 追踪,改动前后对比)
    - eval/reports/report_<时间戳>_<tag>.json (机器可读,便于自动对比)
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import generate_search_strategy   # noqa: E402
from searcher import grep_search           # noqa: E402
from routes.query import _select_files     # noqa: E402

import asyncio  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))


def _norm(name: str) -> str:
    """统一成 basename,golden 存文件名,检索返回相对路径,二者对齐。"""
    return os.path.basename(name).strip()


async def _run_one(q: dict, k: int) -> dict:
    """跑一条:策略 -> 检索 -> 选文件,返回该题的命中情况。"""
    question = q["question"]
    relevant = {_norm(f) for f in q.get("relevant_files", [])}

    t0 = time.perf_counter()
    try:
        strategy, _ = await generate_search_strategy(question)
    except Exception as e:  # noqa: BLE001
        return {"id": q["id"], "error": str(e), "relevant": list(relevant),
                "selected": [], "rank": None, "type": q.get("type", ""),
                "tags": q.get("tags", []), "era": q.get("era", "")}
    keywords = strategy.get("keywords", [question])
    file_pattern = strategy.get("file_pattern", "*")
    results = grep_search(keywords, file_pattern)
    selected = [_norm(f) for f in _select_files(results, max_files=k)]
    # 生产链路含 BM25-RRF 旁路融合，基准须与线上一致
    try:
        from bm25 import fuse_select
        selected = [_norm(f) for f in fuse_select(selected, question, max_files=k)]
    except Exception:  # noqa: BLE001
        pass
    elapsed = time.perf_counter() - t0

    # 第一个命中相关文档的排名(1-based),用于 MRR
    rank = None
    for i, f in enumerate(selected, 1):
        if f in relevant:
            rank = i
            break
    hit = len(relevant & set(selected))
    return {
        "id": q["id"],
        "question": question,
        "type": q.get("type", ""),
        "tags": q.get("tags", []),
        "era": q.get("era", ""),
        "relevant": list(relevant),
        "selected": selected,
        "keywords": keywords,
        "hit_count": hit,
        "rank": rank,
        "elapsed": round(elapsed, 2),
    }


def _metrics(rows: list[dict], k: int) -> dict:
    """计算 Recall@K / Precision@K / MRR / 命中率。"""
    valid = [r for r in rows if "error" not in r]
    if not valid:
        return {"n": 0}
    recalls, precisions, rr = [], [], []
    fully_hit = 0
    for r in valid:
        rel = set(r["relevant"])
        sel = set(r["selected"])
        if not rel:
            continue
        inter = len(rel & sel)
        recalls.append(inter / len(rel))
        precisions.append(inter / max(len(sel), 1))
        rr.append(1.0 / r["rank"] if r["rank"] else 0.0)
        if inter == len(rel):
            fully_hit += 1
    n = len(recalls)
    return {
        "n": n,
        "recall_at_k": round(sum(recalls) / n, 4) if n else 0,
        "precision_at_k": round(sum(precisions) / n, 4) if n else 0,
        "mrr": round(sum(rr) / n, 4) if n else 0,
        "full_hit_rate": round(fully_hit / n, 4) if n else 0,
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(GOLDEN_PATH))
    ap.add_argument("--k", type=int, default=10, help="评测 @K,默认 10(与生产 _select_files 一致)")
    ap.add_argument("--tag", default="", help="报告标签,如 baseline / after-idf")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    print(f"评测集: {args.golden}  共 {len(golden)} 题  @K={args.k}")

    sem = asyncio.Semaphore(args.concurrency)

    async def _guarded(q):
        async with sem:
            return await _run_one(q, args.k)

    t_start = time.perf_counter()
    rows = await asyncio.gather(*[_guarded(q) for q in golden])
    total_time = time.perf_counter() - t_start

    overall = _metrics(rows, args.k)
    by_type = {}
    grouped = defaultdict(list)
    for r in rows:
        grouped[r.get("type", "")].append(r)
    for t, rs in grouped.items():
        by_type[t] = _metrics(rs, args.k)

    # 按活动期分组
    by_era = {}
    g_era = defaultdict(list)
    for r in rows:
        g_era[r.get("era", "")].append(r)
    for e, rs in g_era.items():
        by_era[e] = _metrics(rs, args.k)

    # 按标签分组（一题多标签，分别计入各标签）
    by_tag = {}
    g_tag = defaultdict(list)
    for r in rows:
        for tg in r.get("tags", []):
            g_tag[tg].append(r)
    for tg, rs in g_tag.items():
        by_tag[tg] = _metrics(rs, args.k)

    errors = [r for r in rows if "error" in r]

    # ---- 终端输出 ----
    print(f"\n=== 总体 (@{args.k}) ===")
    print(f"  题数: {overall.get('n', 0)}   错误: {len(errors)}   总耗时: {total_time:.1f}s")
    print(f"  Recall@{args.k}    : {overall.get('recall_at_k', 0)}")
    print(f"  Precision@{args.k} : {overall.get('precision_at_k', 0)}")
    print(f"  MRR           : {overall.get('mrr', 0)}")
    print(f"  全命中率      : {overall.get('full_hit_rate', 0)}")
    print("\n=== 分类型 ===")
    for t in sorted(by_type):
        m = by_type[t]
        print(f"  {t:<8} n={m.get('n',0):<3} Recall={m.get('recall_at_k',0):<6} "
              f"MRR={m.get('mrr',0):<6} Prec={m.get('precision_at_k',0)}")
    print("\n=== 分活动期 ===")
    for e in sorted(by_era):
        m = by_era[e]
        print(f"  {e:<10} n={m.get('n',0):<3} Recall={m.get('recall_at_k',0):<6} MRR={m.get('mrr',0)}")
    print("\n=== 分标签 ===")
    for tg in sorted(by_tag, key=lambda x: -by_tag[x].get('n', 0)):
        m = by_tag[tg]
        print(f"  {tg:<10} n={m.get('n',0):<3} Recall={m.get('recall_at_k',0):<6} MRR={m.get('mrr',0)}")

    # ---- 写报告 ----
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    stem = f"report_{ts}{tag}"

    report_json = {
        "timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag,
        "k": args.k,
        "golden_set": args.golden,
        "n_questions": len(golden),
        "overall": overall,
        "by_type": by_type,
        "by_era": by_era,
        "by_tag": by_tag,
        "errors": [{"id": e["id"], "error": e["error"]} for e in errors],
        "details": rows,
    }
    (REPORTS_DIR / f"{stem}.json").write_text(
        json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_md_report(REPORTS_DIR / f"{stem}.md", report_json, total_time)
    print(f"\n报告已写入: eval/reports/{stem}.md  (+ .json)")


def _write_md_report(path: Path, rep: dict, total_time: float):
    k = rep["k"]
    o = rep["overall"]
    lines = [
        f"# 搜索评测报告 {rep['timestamp']}",
        "",
        f"- 标签: `{rep['tag'] or '(none)'}`",
        f"- 评测集: `{rep['golden_set']}`  共 {rep['n_questions']} 题  @K={k}",
        f"- 总耗时: {total_time:.1f}s   错误: {len(rep['errors'])}",
        "",
        "## 总体指标",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| Recall@{k} | {o.get('recall_at_k', 0)} |",
        f"| Precision@{k} | {o.get('precision_at_k', 0)} |",
        f"| MRR | {o.get('mrr', 0)} |",
        f"| 全命中率 | {o.get('full_hit_rate', 0)} |",
        "",
        "## 分类型指标",
        "",
        "| 类型 | 题数 | Recall | MRR | Precision |",
        "|------|------|--------|-----|-----------|",
    ]
    for t in sorted(rep["by_type"]):
        m = rep["by_type"][t]
        lines.append(f"| {t} | {m.get('n',0)} | {m.get('recall_at_k',0)} | "
                     f"{m.get('mrr',0)} | {m.get('precision_at_k',0)} |")
    lines += ["", "## 分活动期指标", "",
              "| 活动期 | 题数 | Recall | MRR |", "|------|------|--------|-----|"]
    for e in sorted(rep.get("by_era", {})):
        m = rep["by_era"][e]
        lines.append(f"| {e} | {m.get('n',0)} | {m.get('recall_at_k',0)} | {m.get('mrr',0)} |")
    lines += ["", "## 分标签指标", "",
              "| 标签 | 题数 | Recall | MRR |", "|------|------|--------|-----|"]
    for tg in sorted(rep.get("by_tag", {}), key=lambda x: -rep["by_tag"][x].get("n", 0)):
        m = rep["by_tag"][tg]
        lines.append(f"| {tg} | {m.get('n',0)} | {m.get('recall_at_k',0)} | {m.get('mrr',0)} |")
    lines += ["", "## 未命中 / 弱命中明细 (rank 为空或无交集)", ""]
    for r in rep["details"]:
        if "error" in r:
            lines.append(f"- [ERR] #{r['id']} {r.get('question','')[:40]} :: {r['error'][:60]}")
            continue
        if r.get("hit_count", 0) == 0:
            lines.append(f"- #{r['id']} [{r['type']}] {r['question'][:50]}")
            lines.append(f"    - 期望: {r['relevant']}")
            lines.append(f"    - 关键词: {r.get('keywords')}")
            lines.append(f"    - 选中: {r['selected'][:5]}")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
