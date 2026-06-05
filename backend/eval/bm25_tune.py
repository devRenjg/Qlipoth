"""BM25 优化实验（Task7）：k1/b 网格调参 + BM25⊕baseline RRF 融合。

回答"沿 BM25 深挖还有多少空间"。两条独立杠杆，全部复用 strategy_cache
（零 LLM、秒级复跑），隔离在 eval/，不碰生产检索内核。

实验 1（调参）：现行 BM25 用教科书默认 k1=1.5/b=0.75，从未在本语料上调过。
  本语料 628 篇文档长度分布极宽，长度归一项 b 大概率非最优。
  在 golden set 上做 (k1,b) 网格搜索，找本语料最优点。

实验 2（融合）：分类型数据显示 BM25 在排查/歧义类碾压、但负责人/方案类输给
  现行 LLM+grep（LLM 懂"@人名=负责人"语义）。把两路 RRF 融合，吃两边强项。
  扫描融合权重，看整体能否超过纯 BM25 的 0.79。

用法:
    py -3.12 eval/bm25_tune.py
输出: eval/reports/bm25_tune_<时间戳>.md (+ .json)
"""
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from searcher import grep_search          # noqa: E402
from routes.query import _select_files    # noqa: E402
from config import load_settings          # noqa: E402
from eval.bm25_index import tokenize      # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
CACHE_PATH = Path(__file__).parent / "strategy_cache.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))

K = 10
K1_GRID = [0.6, 0.9, 1.2, 1.5, 1.8, 2.2, 2.6]
B_GRID = [0.0, 0.2, 0.4, 0.55, 0.7, 0.75, 0.85, 1.0]
# 融合权重：BM25 路权重 wb，baseline 路权重 1。RRF 里体现为名次贡献的倍数。
FUSION_WB = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
RRF_K = 60


def _norm(name: str) -> str:
    import os
    return os.path.basename(name).strip()


class InvIndex:
    """倒排索引，支持参数化 k1/b 打分（只触达含词文档，网格搜索够快）。"""

    def __init__(self):
        self.doc_names: list[str] = []
        self.doc_len: list[int] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)  # term -> [(doc_idx, tf)]
        self.df: dict[str, int] = {}
        self.avgdl: float = 0.0
        self.n: int = 0

    def build(self, kb_dir: str):
        files = sorted(f for f in Path(kb_dir).rglob("*.md") if f.is_file())
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            toks = tokenize(text)
            tf = Counter(toks)
            idx = len(self.doc_names)
            self.doc_names.append(f.name)
            self.doc_len.append(len(toks))
            for term, c in tf.items():
                self.postings[term].append((idx, c))
        self.n = len(self.doc_names)
        self.avgdl = sum(self.doc_len) / self.n if self.n else 0.0
        self.df = {t: len(p) for t, p in self.postings.items()}
        return self

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)

    def rank(self, query: str, k1: float, b: float, k: int = K) -> list[str]:
        scores = defaultdict(float)
        for term in set(tokenize(query)):
            if term not in self.postings:
                continue
            idf = self._idf(term)
            for idx, f in self.postings[term]:
                denom = f + k1 * (1 - b + b * self.doc_len[idx] / self.avgdl)
                scores[idx] += idf * f * (k1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda x: (-x[1], self.doc_names[x[0]]))
        return [self.doc_names[i] for i, s in ranked[:k] if s > 0]


def _metrics(rows, key):
    """rows: [{relevant:set, key->ranked list}]. 返回 recall/mrr/full_hit。"""
    recalls, rr, full, n = [], [], 0, 0
    for r in rows:
        rel = r["relevant"]
        if not rel:
            continue
        sel = r[key]
        inter = len(rel & set(sel))
        recalls.append(inter / len(rel))
        rank = next((i for i, f in enumerate(sel, 1) if f in rel), None)
        rr.append(1.0 / rank if rank else 0.0)
        if inter == len(rel):
            full += 1
        n += 1
    if not n:
        return {"n": 0, "recall": 0, "mrr": 0, "full_hit": 0}
    return {"n": n, "recall": round(sum(recalls) / n, 4),
            "mrr": round(sum(rr) / n, 4), "full_hit": round(full / n, 4)}


def _rrf(rankings_with_weight, k=K):
    """rankings_with_weight: [(ranking_list, weight)]. 加权 RRF。"""
    score = defaultdict(float)
    for ranking, w in rankings_with_weight:
        for rank, name in enumerate(ranking, 1):
            score[name] += w / (RRF_K + rank)
    return [n for n, _ in sorted(score.items(), key=lambda x: -x[1])[:k]]


def _build_baseline_rows(golden, cache):
    """对每题预跑 grep → baseline 选文件，缓存到磁盘（融合实验复用，复跑秒级）。"""
    cache_file = REPORTS_DIR / "_baseline_grep_cache.json"
    if cache_file.exists():
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        rows = []
        for r in saved:
            r["relevant"] = set(r["relevant"])
            rows.append(r)
        print(f"  复用 grep 缓存 {cache_file.name}（{len(rows)} 题）")
        return rows
    rows = []
    for q in golden:
        rel = set(_norm(f) for f in q.get("relevant_files", []))
        c = cache.get(str(q["id"]))
        if not c or "error" in c:
            continue
        keywords = c["keywords"]
        file_pattern = c.get("file_pattern", "*")
        results = grep_search(keywords, file_pattern)
        baseline = [_norm(f) for f in _select_files(results, max_files=K)]
        rows.append({"id": q["id"], "type": q.get("type", ""),
                     "question": q["question"], "relevant": rel,
                     "baseline": baseline})
    REPORTS_DIR.mkdir(exist_ok=True)
    dump = [{**r, "relevant": sorted(r["relevant"])} for r in rows]
    cache_file.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def main():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    kb = load_settings().knowledge_base_dir

    print("构建倒排索引...", flush=True)
    t0 = time.perf_counter()
    idx = InvIndex().build(kb)
    print(f"  docs={idx.n} vocab={len(idx.df)} avgdl={idx.avgdl:.0f}  {time.perf_counter()-t0:.1f}s")

    print("预跑 baseline（grep+选文件）...", flush=True)
    t0 = time.perf_counter()
    rows = _build_baseline_rows(golden, cache)
    print(f"  {len(rows)} 题就绪  {time.perf_counter()-t0:.1f}s")

    # ---- 实验1：k1/b 网格搜索 ----
    print("\n=== 实验1：k1/b 网格搜索 ===", flush=True)
    grid = []
    for k1 in K1_GRID:
        for b in B_GRID:
            for r in rows:
                r["_bm"] = idx.rank(r["question"], k1, b)
            m = _metrics(rows, "_bm")
            grid.append({"k1": k1, "b": b, **m})
    grid.sort(key=lambda x: (-x["recall"], -x["mrr"]))
    baseline_default = next(g for g in grid if g["k1"] == 1.5 and g["b"] == 0.75)
    best = grid[0]
    print(f"  默认 k1=1.5/b=0.75 : Recall={baseline_default['recall']} MRR={baseline_default['mrr']}")
    print(f"  最优 k1={best['k1']}/b={best['b']} : Recall={best['recall']} MRR={best['mrr']}")
    print("  Top5:")
    for g in grid[:5]:
        print(f"    k1={g['k1']:<4} b={g['b']:<5} Recall={g['recall']} MRR={g['mrr']} Full={g['full_hit']}")

    # 用最优参数固定 BM25 排名（供融合实验与分类型）
    bk1, bb = best["k1"], best["b"]
    for r in rows:
        r["bm_best"] = idx.rank(r["question"], bk1, bb)
        r["bm_def"] = idx.rank(r["question"], 1.5, 0.75)

    # ---- 实验2：BM25(最优参数) ⊕ baseline RRF 融合 ----
    print("\n=== 实验2：BM25⊕baseline RRF 融合（扫描 BM25 权重）===", flush=True)
    fusion = []
    for wb in FUSION_WB:
        for r in rows:
            r["_fz"] = _rrf([(r["bm_best"], wb), (r["baseline"], 1.0)])
        m = _metrics(rows, "_fz")
        fusion.append({"wb": wb, **m})
        print(f"  wb={wb:<4} Recall={m['recall']} MRR={m['mrr']} Full={m['full_hit']}")
    fusion.sort(key=lambda x: (-x["recall"], -x["mrr"]))
    best_fz = fusion[0]
    for r in rows:
        r["fz_best"] = _rrf([(r["bm_best"], best_fz["wb"]), (r["baseline"], 1.0)])

    # ---- 参照线 ----
    # 生产现状 = fuse_select(RRF 等权, B=0.75)；改后 = 同结构但 B=最优。
    # 这两行才是"上线决策"的精确对照（生产用的就是 RRF 等权融合，wb=1.0）。
    for r in rows:
        r["bm_prod"] = idx.rank(r["question"], 1.5, 0.75)          # 生产现网 BM25 排名
        r["fz_prod"] = _rrf([(r["baseline"], 1.0), (r["bm_prod"], 1.0)])   # 生产现状融合
        r["fz_tuned"] = _rrf([(r["baseline"], 1.0), (r["bm_best"], 1.0)])  # 仅改 B 后融合
    ref = {
        "baseline(grep+IDF)": _metrics(rows, "baseline"),
        "bm_default(1.5/0.75)": _metrics(rows, "bm_def"),
        "bm_tuned(最优k1/b)": _metrics(rows, "bm_best"),
        "融合-生产现状(B=0.75)": _metrics(rows, "fz_prod"),
        "融合-调参后(B=最优)": _metrics(rows, "fz_tuned"),
        "融合-最优权重(扫wb)": _metrics(rows, "fz_best"),
    }
    print("\n=== 总体对照 ===")
    for name, m in ref.items():
        print(f"  {name:<22} Recall={m['recall']} MRR={m['mrr']} Full={m['full_hit']}")

    # ---- 分类型（看融合是否补齐短板）----
    types = sorted(set(r["type"] for r in rows))
    by_type = {}
    for t in types:
        sub = [r for r in rows if r["type"] == t]
        by_type[t] = {
            "n": len(sub),
            "baseline": _metrics(sub, "baseline")["recall"],
            "bm_tuned": _metrics(sub, "bm_best")["recall"],
            "fusion_prod": _metrics(sub, "fz_prod")["recall"],
            "fusion_tuned": _metrics(sub, "fz_tuned")["recall"],
        }
    print("\n=== 分类型 Recall@10 ===")
    print(f"  {'类型':<10}{'baseline':>10}{'bm_tuned':>10}{'fz_prod':>9}{'fz_tuned':>10}")
    for t in types:
        d = by_type[t]
        print(f"  {t:<10}{d['baseline']:>10}{d['bm_tuned']:>10}"
              f"{d['fusion_prod']:>9}{d['fusion_tuned']:>10}  (n={d['n']})")

    # ---- 写报告 ----
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {
        "timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "n": len(rows), "k": K, "avgdl": round(idx.avgdl, 1),
        "grid": grid, "default": baseline_default, "best_param": best,
        "fusion": fusion, "best_fusion": best_fz,
        "ref": ref, "by_type": by_type,
    }
    (REPORTS_DIR / f"bm25_tune_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"bm25_tune_{ts}.md", rep)
    print(f"\n报告: eval/reports/bm25_tune_{ts}.md (+ .json)")


def _write_md(path, rep):
    r = rep
    lines = [
        f"# BM25 优化实验（调参 + 融合） {r['timestamp']}",
        "",
        f"- 评测集 {r['n']} 题 @K={r['k']}，零 LLM（复用 strategy_cache），avgdl={r['avgdl']}",
        "- 两条杠杆：① k1/b 网格调参 ② BM25⊕baseline RRF 融合",
        "",
        "## 总体对照",
        "",
        "| 方案 | Recall@10 | MRR | 全命中率 |",
        "|------|-----------|-----|----------|",
    ]
    for name, m in r["ref"].items():
        lines.append(f"| {name} | {m['recall']} | {m['mrr']} | {m['full_hit']} |")
    d, bp, bf = r["default"], r["best_param"], r["best_fusion"]
    lines += [
        "",
        "## 实验1：k1/b 网格搜索",
        "",
        f"- 默认 **k1=1.5 / b=0.75**：Recall={d['recall']} MRR={d['mrr']}",
        f"- 最优 **k1={bp['k1']} / b={bp['b']}**：Recall={bp['recall']} MRR={bp['mrr']}"
        f"（Δrecall={round(bp['recall']-d['recall'],4)}）",
        "",
        "| k1 | b | Recall | MRR | 全命中 |",
        "|----|----|--------|-----|--------|",
    ]
    for g in r["grid"][:10]:
        lines.append(f"| {g['k1']} | {g['b']} | {g['recall']} | {g['mrr']} | {g['full_hit']} |")
    lines += [
        "",
        "## 实验2：BM25⊕baseline RRF 融合",
        "",
        f"- 最优 BM25 权重 **wb={bf['wb']}**：Recall={bf['recall']} MRR={bf['mrr']}",
        "",
        "| wb | Recall | MRR | 全命中 |",
        "|----|--------|-----|--------|",
    ]
    for f in r["fusion"]:
        lines.append(f"| {f['wb']} | {f['recall']} | {f['mrr']} | {f['full_hit']} |")
    lines += ["", "## 分类型 Recall@10", "",
              "| 类型 | n | baseline | bm_tuned | 融合(现状B=0.75) | 融合(调参B=最优) |",
              "|------|---|----------|----------|------------------|------------------|"]
    for t in sorted(r["by_type"]):
        d = r["by_type"][t]
        lines.append(f"| {t} | {d['n']} | {d['baseline']} | {d['bm_tuned']} | "
                     f"{d['fusion_prod']} | {d['fusion_tuned']} |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
