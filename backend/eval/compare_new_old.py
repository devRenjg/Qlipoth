# -*- coding: utf-8 -*-
"""新旧库受控对比：同100题，分别在「全库(目标文档=API新版)」vs「全库(目标文档=Playwright旧版)」上跑检索。
两库其余文档相同、召回池一致，唯一区别是golden引用的59篇用新版/旧版，对比纯净。
输出 Recall@10/MRR 及分type对比。"""
import asyncio, json, sys, os, shutil, tempfile
from pathlib import Path
from collections import defaultdict

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
import searcher
import llm
from config import load_settings
from routes.query import _select_files
from eval.bm25_index import BM25Index, tokenize

KB = BACKEND_DIR / "knowledge_base"
OLD = BACKEND_DIR / "knowledge_base_old"
GOLDEN = json.load(open(Path(__file__).parent / "golden_set.json", encoding="utf-8"))

def _norm(f): return f.strip()

async def _retrieve(q, k):
    """复现线上baseline:LLM关键词→grep→_select_files(IDF覆盖度)。返回topk文件名。"""
    st, _ = await llm.generate_search_strategy(q["question"])
    kws = st.get("keywords", [q["question"]])
    res = searcher.grep_search(kws, st.get("file_pattern", "*"))
    return [_norm(f) for f in _select_files(res, max_files=k)]

def _row_metric(selected, relevant):
    rel = set(relevant); sel = selected
    hit = len(rel & set(sel))
    rank = next((i for i, f in enumerate(sel, 1) if f in rel), None)
    recall = hit / len(rel) if rel else 0
    mrr = 1.0 / rank if rank else 0
    return recall, mrr

async def run_on(kb_dir, k):
    searcher._get_kb_dir = lambda: str(kb_dir)
    rows = []
    for q in GOLDEN:
        rel = [_norm(f) for f in q["relevant_files"]]
        try:
            sel = await _retrieve(q, k)
        except Exception:
            sel = []
        rc, mr = _row_metric(sel, rel)
        rows.append({"type": q["type"], "recall": rc, "mrr": mr})
    return rows

def run_bm25(kb_dir, k):
    """语义化检索:全库 char-bigram BM25,问题直接 topk。"""
    idx = BM25Index(tokenize).build(str(kb_dir))
    rows = []
    for q in GOLDEN:
        rel = [_norm(f) for f in q["relevant_files"]]
        sel = [_norm(f) for f in idx.topk(q["question"], k)]
        rc, mr = _row_metric(sel, rel)
        rows.append({"type": q["type"], "recall": rc, "mrr": mr})
    return rows

def agg(rows):
    n = len(rows)
    return sum(r["recall"] for r in rows)/n, sum(r["mrr"] for r in rows)/n

def agg_by_type(rows):
    bt = defaultdict(list)
    for r in rows: bt[r["type"]].append(r)
    return {t: (sum(x["recall"] for x in v)/len(v), len(v)) for t, v in bt.items()}


def build_old_corpus():
    """复制当前全库到临时目录，把golden引用的59篇换成旧版。返回临时目录路径。"""
    tmp = Path(tempfile.gettempdir()) / "kb_old_compare"
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True)
    for f in os.listdir(KB):
        if f.endswith(".md"): shutil.copy2(KB / f, tmp / f)
    gfiles = set(f for q in GOLDEN for f in q["relevant_files"])
    swapped = 0
    for f in gfiles:
        oldp = OLD / f
        if oldp.exists():
            shutil.copy2(oldp, tmp / f); swapped += 1
    return tmp, swapped

async def main():
    k = 10
    print("构造旧版对比库...", flush=True)
    old_dir, swapped = build_old_corpus()
    print(f"  已将 {swapped} 篇golden文档换成旧版(其余文档与新库相同)", flush=True)

    print("跑【新版库】(API导入)...", flush=True)
    new_rows = await run_on(KB, k)
    print("跑【旧版库】(Playwright)...", flush=True)
    old_rows = await run_on(old_dir, k)

    nr, nm = agg(new_rows); orr, om = agg(old_rows)
    print("\n=== 新旧库受控对比 (100题, Recall@10, 线上baseline检索) ===", flush=True)
    print(f"  新版(API)     : Recall={nr:.3f}  MRR={nm:.3f}", flush=True)
    print(f"  旧版(Playwright): Recall={orr:.3f}  MRR={om:.3f}", flush=True)
    print(f"  提升          : Recall {nr-orr:+.3f}  MRR {nm-om:+.3f}", flush=True)
    print("\n--- 分type Recall (新 vs 旧) ---", flush=True)
    nbt, obt = agg_by_type(new_rows), agg_by_type(old_rows)
    for t in nbt:
        nrc, n = nbt[t]; orc = obt.get(t, (0, 0))[0]
        print(f"  {t}(n={n}): 新{nrc:.2f} vs 旧{orc:.2f}  ({nrc-orc:+.2f})", flush=True)
    json.dump({"new": {"recall": nr, "mrr": nm}, "old": {"recall": orr, "mrr": om},
               "by_type_new": {t: v[0] for t, v in nbt.items()},
               "by_type_old": {t: v[0] for t, v in obt.items()}},
              open(Path(__file__).parent / "reports" / "compare_new_old.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # —— 语义化检索(BM25)对比 ——
    print("\n构建BM25索引并对比(语义化检索)...", flush=True)
    nb = run_bm25(KB, k); ob = run_bm25(old_dir, k)
    nbr, nbm = agg(nb); obr, obm = agg(ob)
    print("\n=== 新旧库受控对比 (100题, Recall@10, BM25语义检索) ===", flush=True)
    print(f"  新版(API)     : Recall={nbr:.3f}  MRR={nbm:.3f}", flush=True)
    print(f"  旧版(Playwright): Recall={obr:.3f}  MRR={obm:.3f}", flush=True)
    print(f"  提升          : Recall {nbr-obr:+.3f}  MRR {nbm-obm:+.3f}", flush=True)
    print("\n--- BM25 分type Recall (新 vs 旧) ---", flush=True)
    nbbt, obbt = agg_by_type(nb), agg_by_type(ob)
    for t in nbbt:
        nrc, n = nbbt[t]; orc = obbt.get(t, (0, 0))[0]
        print(f"  {t}(n={n}): 新{nrc:.2f} vs 旧{orc:.2f}  ({nrc-orc:+.2f})", flush=True)
    json.dump({"grep": {"new": {"recall": nr, "mrr": nm}, "old": {"recall": orr, "mrr": om}},
               "bm25": {"new": {"recall": nbr, "mrr": nbm}, "old": {"recall": obr, "mrr": obm},
                        "by_type_new": {t: v[0] for t, v in nbbt.items()},
                        "by_type_old": {t: v[0] for t, v in obbt.items()}}},
              open(Path(__file__).parent / "reports" / "compare_new_old_full.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

if __name__ == "__main__":
    asyncio.run(main())
