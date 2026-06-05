"""Task5 A/B 评测：对比 4 种检索方案在 golden_set 上的 Recall@K / MRR / Precision@K。

方案：
  baseline  : 现网 LLM策略关键词 → grep_search → _select_files(IDF覆盖度)  ← 线上行为
  bm25_char : 全库 BM25（char-bigram 分词，零依赖），原始问题直接打分 topk
  bm25_jieba: 全库 BM25（jieba 词级分词，rank_bm25 思路），原始问题 topk
  hybrid    : baseline 的 grep 召回集 → 用 bm25_char 在候选内重排（grep 召回 + BM25 精排）

为什么这样设计：现网召回靠 LLM 关键词 + ripgrep（词法精确但怕 OOV/长复合词），
BM25 是统计排序（全库、对原始问题鲁棒）。hybrid 想兼得：grep 高精度召回 + BM25 稳排序。

用法:
    py -3.12 eval/ab_eval.py
    py -3.12 eval/ab_eval.py --k 10
输出: eval/reports/ab_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
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
from config import load_settings           # noqa: E402
from eval.bm25_index import BM25Index, tokenize, tokenize_jieba  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))


def _norm(name: str) -> str:
    return os.path.basename(name).strip()


async def _baseline_and_hybrid(q, k, bm25_char):
    """跑一次 LLM 策略 + grep，复用给 baseline 和 hybrid（省一半 LLM 调用）。"""
    question = q["question"]
    try:
        strategy, _ = await generate_search_strategy(question)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, {"error": str(e)}
    keywords = strategy.get("keywords", [question])
    file_pattern = strategy.get("file_pattern", "*")
    results = grep_search(keywords, file_pattern)
    baseline_sel = [_norm(f) for f in _select_files(results, max_files=k)]
    # hybrid: 在 baseline 选出的候选（放大召回到 3k 再重排）内用 BM25 精排
    wide = [_norm(f) for f in _select_files(results, max_files=max(k * 3, 30))]
    hybrid_sel = bm25_char.rerank(wide, question, k=k)
    return {"selected": baseline_sel, "keywords": keywords}, {"selected": hybrid_sel}


def _eval_row(selected, relevant):
    rel = set(relevant)
    rank = None
    for i, f in enumerate(selected, 1):
        if f in rel:
            rank = i
            break
    return {"selected": selected, "hit_count": len(rel & set(selected)), "rank": rank}


def _metrics(rows, key):
    valid = [r for r in rows if "error" not in r and key in r]
    recalls, precisions, rr, full = [], [], [], 0
    for r in valid:
        rel = set(r["relevant"])
        if not rel:
            continue
        sel = set(r[key]["selected"])
        inter = len(rel & sel)
        recalls.append(inter / len(rel))
        precisions.append(inter / max(len(sel), 1))
        rr.append(1.0 / r[key]["rank"] if r[key]["rank"] else 0.0)
        if inter == len(rel):
            full += 1
    n = len(recalls)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "recall": round(sum(recalls) / n, 4),
        "precision": round(sum(precisions) / n, 4),
        "mrr": round(sum(rr) / n, 4),
        "full_hit": round(full / n, 4),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    kb = load_settings().knowledge_base_dir
    print(f"评测集 {len(golden)} 题  @K={args.k}")

    print("构建 BM25 索引(char-bigram)...", end=" ", flush=True)
    t0 = time.perf_counter()
    bm25_char = BM25Index(tokenize).build(kb)
    print(f"{time.perf_counter()-t0:.1f}s  docs={bm25_char.n} vocab={len(bm25_char.df)}")
    print("构建 BM25 索引(jieba)...", end=" ", flush=True)
    t0 = time.perf_counter()
    bm25_jieba = BM25Index(tokenize_jieba).build(kb)
    print(f"{time.perf_counter()-t0:.1f}s  docs={bm25_jieba.n} vocab={len(bm25_jieba.df)}")

    sem = asyncio.Semaphore(args.concurrency)

    async def _one(q):
        async with sem:
            relevant = [_norm(f) for f in q.get("relevant_files", [])]
            row = {"id": q["id"], "question": q["question"],
                   "type": q.get("type", ""), "relevant": relevant}
            base, hyb = await _baseline_and_hybrid(q, args.k, bm25_char)
            if "error" in base:
                row["error"] = base["error"]
                return row
            row["keywords"] = base.get("keywords")
            row["baseline"] = _eval_row(base["selected"], relevant)
            row["hybrid"] = _eval_row(hyb["selected"], relevant)
            row["bm25_char"] = _eval_row(bm25_char.topk(q["question"], args.k), relevant)
            row["bm25_jieba"] = _eval_row(bm25_jieba.topk(q["question"], args.k), relevant)
            return row

    t_start = time.perf_counter()
    rows = await asyncio.gather(*[_one(q) for q in golden])
    total = time.perf_counter() - t_start

    modes = ["baseline", "bm25_char", "bm25_jieba", "hybrid"]
    overall = {m: _metrics(rows, m) for m in modes}
    grouped = defaultdict(list)
    for r in rows:
        grouped[r.get("type", "")].append(r)
    by_type = {t: {m: _metrics(rs, m) for m in modes} for t, rs in grouped.items()}
    errors = [r for r in rows if "error" in r]

    print(f"\n=== 总体 @{args.k}  ({len(golden)}题, {len(errors)}错误, {total:.1f}s) ===")
    print(f"{'mode':<12}{'Recall':>9}{'MRR':>9}{'Prec':>9}{'FullHit':>9}")
    for m in modes:
        o = overall[m]
        print(f"{m:<12}{o.get('recall',0):>9}{o.get('mrr',0):>9}"
              f"{o.get('precision',0):>9}{o.get('full_hit',0):>9}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    stem = f"ab_{ts}"
    rep = {
        "timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "k": args.k, "n": len(golden), "modes": modes,
        "overall": overall, "by_type": by_type,
        "errors": [{"id": e["id"], "error": e["error"]} for e in errors],
        "rows": rows,
    }
    (REPORTS_DIR / f"{stem}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"{stem}.md", rep, total)
    print(f"\n报告: eval/reports/{stem}.md (+ .json)")


def _write_md(path, rep, total):
    k = rep["k"]
    modes = rep["modes"]
    lines = [
        f"# 检索方案 A/B 评测 {rep['timestamp']}",
        "",
        f"- 评测集 {rep['n']} 题  @K={k}  总耗时 {total:.1f}s  错误 {len(rep['errors'])}",
        "",
        "## 方案说明",
        "- **baseline**: 线上行为 = LLM策略关键词 → ripgrep → IDF覆盖度选文件",
        "- **bm25_char**: 全库 BM25，中文 char-bigram 分词，原始问题直接 topk（零依赖）",
        "- **bm25_jieba**: 全库 BM25，jieba 词级分词，原始问题 topk",
        "- **hybrid**: baseline 的 grep 候选 → BM25(char) 在候选内重排",
        "",
        "## 总体指标",
        "",
        "| 方案 | Recall@{0} | MRR | Precision@{0} | 全命中率 |".format(k),
        "|------|-----------|-----|---------------|----------|",
    ]
    for m in modes:
        o = rep["overall"][m]
        lines.append(f"| {m} | {o.get('recall',0)} | {o.get('mrr',0)} | "
                     f"{o.get('precision',0)} | {o.get('full_hit',0)} |")
    lines += ["", "## 分类型 Recall@{0}".format(k), "",
              "| 类型 | " + " | ".join(modes) + " |",
              "|------|" + "|".join(["------"] * len(modes)) + "|"]
    for t in sorted(rep["by_type"]):
        cells = [f"{rep['by_type'][t][m].get('recall',0)}" for m in modes]
        n = rep["by_type"][t][modes[0]].get("n", 0)
        lines.append(f"| {t}(n={n}) | " + " | ".join(cells) + " |")
    lines += ["", "## baseline 漏召但其他方案命中的题（增量价值）", ""]
    for r in rep["rows"]:
        if "error" in r:
            continue
        bh = r["baseline"]["hit_count"]
        gains = [m for m in modes[1:] if r[m]["hit_count"] > bh]
        if bh == 0 and gains:
            lines.append(f"- #{r['id']} [{r['type']}] {r['question'][:46]}")
            lines.append(f"    - 期望 {r['relevant']}  被【{','.join(gains)}】捞回")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
