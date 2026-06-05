"""Task6 知识库范式横评：现行 grep+LLM vs gbrain-lite vs cognee-lite vs BM25。

对比 6 种检索方案在同一 golden_set（100题，每题1个ground-truth文档）上的
Recall@K / MRR / Precision@K，全部走同一次 LLM 策略调用（公平、省成本）。

方案：
  baseline    : 线上行为 = LLM策略关键词 → ripgrep → IDF覆盖度选文件
  bm25_char   : 全库 BM25（char-bigram，零依赖），原始问题直接 topk
  gbrain_lite : baseline 候选 → 文档关系图（父子/共享owner）1-hop 扩展重排
  cognee_lite : 实体共现图谱（owner/服务名/常量/标题词）实体匹配 + 1-hop
  hybrid_bm25 : baseline 候选 → BM25(char) 重排（现网在用的旁路重排）
  cog+grep    : cognee_lite 与 baseline 的 RRF 融合（图谱+词法互补上界探测）

环境说明：气隙环境无 embedding 端点，Khoj 全向量 / Cognee 向量半不可实跑，
仅在报告中做架构层评估。本脚本只读、隔离在 eval/，不触碰生产检索内核。

用法:
    py -3.12 eval/paradigm_eval.py --k 10
输出: eval/reports/paradigm_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import generate_search_strategy        # noqa: E402
from searcher import grep_search                # noqa: E402
from routes.query import _select_files          # noqa: E402
from config import load_settings                # noqa: E402
from eval.bm25_index import BM25Index, tokenize  # noqa: E402
from eval.graph_index import DocGraph, EntityGraph  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
CACHE_PATH = Path(__file__).parent / "strategy_cache.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))

MODES = ["baseline", "bm25_char", "gbrain_lite", "cognee_lite", "hybrid_bm25", "cog_grep"]


def _norm(name: str) -> str:
    return os.path.basename(name).strip()


def _rrf(*rankings, k_rrf=60, k=10):
    """Reciprocal Rank Fusion 多路排序融合。"""
    score = defaultdict(float)
    for ranking in rankings:
        for rank, name in enumerate(ranking, 1):
            score[name] += 1.0 / (k_rrf + rank)
    return [n for n, _ in sorted(score.items(), key=lambda x: -x[1])[:k]]


async def _run_one(q, k, bm25, docg, entg, cache=None):
    """派生所有方案的 topk。策略优先用缓存（零 LLM），无缓存才实时调用。"""
    question = q["question"]
    cached = (cache or {}).get(str(q["id"]))
    if cached and "error" not in cached:
        keywords = cached["keywords"]
        file_pattern = cached.get("file_pattern", "*")
    else:
        try:
            strategy, _ = await generate_search_strategy(question)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        keywords = strategy.get("keywords", [question])
        file_pattern = strategy.get("file_pattern", "*")
    results = grep_search(keywords, file_pattern)

    baseline_sel = [_norm(f) for f in _select_files(results, max_files=k)]
    wide = [_norm(f) for f in _select_files(results, max_files=max(k * 3, 30))]

    # base_scores：用 grep 选文件的 rank 反比作为初始权重
    base_scores = {f: 1.0 / (i + 1) for i, f in enumerate(wide)}

    out = {
        "keywords": keywords,
        "baseline": baseline_sel,
        "bm25_char": bm25.topk(question, k),
        "gbrain_lite": docg.expand_rerank(wide, base_scores, k),
        "cognee_lite": entg.topk(question, keywords, k),
        "hybrid_bm25": bm25.rerank(wide, question, k),
    }
    out["cog_grep"] = _rrf(out["cognee_lite"], baseline_sel, k=k)
    return out


def _metrics(rows, mode):
    valid = [r for r in rows if "error" not in r]
    recalls, precisions, rr, full = [], [], [], 0
    for r in valid:
        rel = set(r["relevant"])
        if not rel:
            continue
        sel = r["sel"][mode]
        inter = len(rel & set(sel))
        recalls.append(inter / len(rel))
        precisions.append(inter / max(len(sel), 1))
        rank = next((i for i, f in enumerate(sel, 1) if f in rel), None)
        rr.append(1.0 / rank if rank else 0.0)
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
    cache = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        n_ok = sum(1 for v in cache.values() if "error" not in v)
        print(f"策略缓存命中 {n_ok}/{len(golden)} 题（缺的实时调用 LLM）")
    print(f"评测集 {len(golden)} 题  @K={args.k}")

    print("构建索引...", flush=True)
    t0 = time.perf_counter()
    bm25 = BM25Index(tokenize).build(kb)
    t_bm25 = time.perf_counter() - t0
    print(f"  BM25(char):   {t_bm25:.1f}s  docs={bm25.n} vocab={len(bm25.df)}")
    t0 = time.perf_counter()
    docg = DocGraph().build(kb)
    t_docg = time.perf_counter() - t0
    n_parent = sum(1 for p in docg.parent.values() if p)
    n_owner = sum(1 for o in docg.owners.values() if o)
    print(f"  DocGraph:     {t_docg:.1f}s  docs={len(docg.names)} 有父文档={n_parent} 有owner={n_owner}")
    t0 = time.perf_counter()
    entg = EntityGraph().build(kb)
    t_entg = time.perf_counter() - t0
    print(f"  EntityGraph:  {t_entg:.1f}s  docs={len(entg.names)} 实体={len(entg.ent_docs)}")

    sem = asyncio.Semaphore(args.concurrency)

    async def _one(q):
        async with sem:
            relevant = [_norm(f) for f in q.get("relevant_files", [])]
            row = {"id": q["id"], "question": q["question"],
                   "type": q.get("type", ""), "relevant": relevant}
            res = await _run_one(q, args.k, bm25, docg, entg, cache)
            if "error" in res:
                row["error"] = res["error"]
                return row
            row["keywords"] = res["keywords"]
            row["sel"] = {m: res[m] for m in MODES}
            return row

    t_start = time.perf_counter()
    rows = await asyncio.gather(*[_one(q) for q in golden])
    total = time.perf_counter() - t_start

    overall = {m: _metrics(rows, m) for m in MODES}
    grouped = defaultdict(list)
    for r in rows:
        grouped[r.get("type", "")].append(r)
    by_type = {t: {m: _metrics(rs, m) for m in MODES} for t, rs in grouped.items()}
    errors = [r for r in rows if "error" in r]

    print(f"\n=== 总体 @{args.k}  ({len(golden)}题, {len(errors)}错误, {total:.1f}s) ===")
    print(f"{'mode':<14}{'Recall':>9}{'MRR':>9}{'Prec':>9}{'FullHit':>9}")
    for m in MODES:
        o = overall[m]
        print(f"{m:<14}{o.get('recall',0):>9}{o.get('mrr',0):>9}"
              f"{o.get('precision',0):>9}{o.get('full_hit',0):>9}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    stem = f"paradigm_{ts}"
    rep = {
        "timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "k": args.k, "n": len(golden), "modes": MODES,
        "index_cost": {"bm25_s": round(t_bm25, 1), "docgraph_s": round(t_docg, 1),
                       "entitygraph_s": round(t_entg, 1),
                       "entity_count": len(entg.ent_docs)},
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
    ic = rep["index_cost"]
    lines = [
        f"# 知识库范式横评 {rep['timestamp']}",
        "",
        f"- 评测集 {rep['n']} 题  @K={k}  总耗时 {total:.1f}s  错误 {len(rep['errors'])}",
        f"- 索引构建成本: BM25 {ic['bm25_s']}s / DocGraph {ic['docgraph_s']}s / "
        f"EntityGraph {ic['entitygraph_s']}s（实体 {ic['entity_count']} 个）",
        "",
        "## 方案说明",
        "- **baseline**: 线上 = LLM策略关键词 → ripgrep → IDF覆盖度选文件",
        "- **bm25_char**: 全库 BM25，char-bigram 分词，原始问题 topk（零依赖）",
        "- **gbrain_lite**: baseline 候选 → 文档关系图(父子/共享owner) 1-hop 扩展重排",
        "- **cognee_lite**: 实体共现图谱(owner/服务名/常量/标题词) 实体匹配 + 1-hop",
        "- **hybrid_bm25**: baseline 候选 → BM25 重排（现网旁路重排）",
        "- **cog_grep**: cognee_lite ⊕ baseline 的 RRF 融合（图谱+词法互补上界）",
        "",
        "## 总体指标",
        "",
        f"| 方案 | Recall@{k} | MRR | Precision@{k} | 全命中率 |",
        "|------|-----------|-----|---------------|----------|",
    ]
    for m in modes:
        o = rep["overall"][m]
        lines.append(f"| {m} | {o.get('recall',0)} | {o.get('mrr',0)} | "
                     f"{o.get('precision',0)} | {o.get('full_hit',0)} |")
    lines += ["", f"## 分类型 Recall@{k}", "",
              "| 类型 | " + " | ".join(modes) + " |",
              "|------|" + "|".join(["------"] * len(modes)) + "|"]
    for t in sorted(rep["by_type"]):
        cells = [f"{rep['by_type'][t][m].get('recall',0)}" for m in modes]
        n = rep["by_type"][t][modes[0]].get("n", 0)
        lines.append(f"| {t}(n={n}) | " + " | ".join(cells) + " |")
    # 逐题：baseline 漏召但图谱/BM25 捞回（增量价值）
    lines += ["", "## baseline 漏召、其他方案命中的题（增量价值）", ""]
    for r in rep["rows"]:
        if "error" in r:
            continue
        rel = set(r["relevant"])
        bh = len(rel & set(r["sel"]["baseline"]))
        gains = [m for m in modes[1:] if len(rel & set(r["sel"][m])) > bh]
        if bh == 0 and gains:
            lines.append(f"- #{r['id']} [{r['type']}] {r['question'][:42]}")
            lines.append(f"    - 期望 {r['relevant']} 被【{','.join(gains)}】捞回")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
