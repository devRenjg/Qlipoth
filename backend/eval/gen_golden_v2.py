# -*- coding: utf-8 -*-
"""Golden Set v2 (100题) 生成：从已API重导的文档分层抽样反向出题，
覆盖8类(含新增 表格数据/跨文档综合/精确数值)。输出候选供质检+裁剪。

用法: py -3.12 eval/gen_golden_v2.py
"""
import asyncio, json, random, sys, re, os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from llm import chat_completion          # noqa
from eval.golden_meta import era_of, tags_of  # noqa
import sqlite3
from database import DB_PATH

KB = BACKEND_DIR / "knowledge_base"
OUT = Path(__file__).parent / "golden_v2_candidates.json"

# 仅从已API重导的文档出题(反映新库真实内容)
def reimported_docs():
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
    rows = db.execute("SELECT stored_path FROM documents WHERE has_old_version=1").fetchall()
    db.close()
    return [r["stored_path"] for r in rows if (KB / r["stored_path"]).exists()]

def read_doc(name, limit=4000):
    t = (KB / name).read_text(encoding="utf-8", errors="replace")
    return t[:limit]

# 单文档出题(5传统类 + 表格数据 + 精确数值)
SINGLE_PROMPT = """你在为"大型直播活动保障知识库"构建搜索评测集。下面是一篇文档内容。
站在直播保障团队同事角度，生成 {n} 个他们真实会问、且答案确实在这篇文档里的问题。
要求：
- 口语化、简短自然，不抄标题/原文
- 覆盖类型，每题标注type，从中选：数量类、排查类、负责人类、方案类、表格数据类、精确数值类
  · 表格数据类：答案在表格里、要定位某行某列的字段值(资源名/负责人/状态/参数等)
  · 精确数值类：答案是具体数值(PCU/带宽/QPS/DAU/码率/时间等)
- 避免任何文档都能答的宽泛问题
返回严格JSON数组：[{{"question":"...","type":"..."}}]，只输出JSON。

文档《{title}》：
{body}"""

# 跨文档综合出题(给同主题多篇，生成需综合的问题)
CROSS_PROMPT = """你在为"大型直播活动保障知识库"构建搜索评测集。下面是同一主题下的 {k} 篇相关文档。
生成 {n} 个需要【综合其中2篇及以上】才能完整回答的问题(单看一篇答不全)。
要求：口语化、明确、答案分布在多篇里。返回严格JSON：
[{{"question":"...","type":"跨文档综合类","need_docs":[文档序号从1开始,如[1,2]]}}]，只输出JSON。

{blocks}"""

def strip_fence(t):
    t = t.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?", "", t); t = re.sub(r"\n?```$", "", t)
    return t.strip()

async def gen_single(name, n, sem):
    async with sem:
        try:
            txt, _ = await chat_completion(
                [{"role": "user", "content": SINGLE_PROMPT.format(n=n, title=name, body=read_doc(name))}],
                temperature=0.4)
            arr = json.loads(strip_fence(txt))
            out = []
            for it in arr:
                if it.get("question") and it.get("type"):
                    out.append({"question": it["question"].strip(), "type": it["type"].strip(),
                                "relevant_files": [name], "tags": tags_of(name), "era": era_of(name)})
            return out
        except Exception:
            return []

async def gen_cross(names, n, sem):
    async with sem:
        try:
            blocks = "\n\n".join(f"【文档{i+1}：{nm}】\n{read_doc(nm, 1800)}" for i, nm in enumerate(names))
            txt, _ = await chat_completion(
                [{"role": "user", "content": CROSS_PROMPT.format(k=len(names), n=n, blocks=blocks)}],
                temperature=0.5)
            arr = json.loads(strip_fence(txt))
            out = []
            for it in arr:
                idxs = it.get("need_docs") or list(range(1, len(names)+1))
                files = [names[i-1] for i in idxs if 1 <= i <= len(names)]
                if it.get("question") and len(files) >= 2:
                    tags = sorted(set(sum([tags_of(f) for f in files], [])))
                    out.append({"question": it["question"].strip(), "type": "跨文档综合类",
                                "relevant_files": files, "tags": tags, "era": era_of(files[0])})
            return out
        except Exception:
            return []


# 按主题分组(用tags聚类同主题文档,供跨文档出题)
def group_by_tag(docs):
    groups = {}
    for d in docs:
        for t in tags_of(d):
            groups.setdefault(t, []).append(d)
    return {k: v for k, v in groups.items() if len(v) >= 2}

async def main():
    random.seed(2026)
    docs = reimported_docs()
    print(f"已重导文档 {len(docs)} 篇，开始生成候选...", flush=True)
    sem = asyncio.Semaphore(5)

    # 1) 单文档题:抽样~55篇，每篇3题(产出~165候选，覆盖6个单文档类型)
    single_docs = random.sample(docs, min(55, len(docs)))
    single_tasks = [gen_single(d, 3, sem) for d in single_docs]

    # 2) 跨文档题:从tag分组里挑12组、每组2-3篇，每组2题
    groups = group_by_tag(docs)
    gkeys = list(groups.keys()); random.shuffle(gkeys)
    cross_tasks = []
    for k in gkeys[:12]:
        picks = random.sample(groups[k], min(3, len(groups[k])))
        cross_tasks.append(gen_cross(picks, 2, sem))

    results = await asyncio.gather(*single_tasks, *cross_tasks)
    cand = [q for sub in results for q in sub]
    # 去重(同question)
    seen = set(); uniq = []
    for q in cand:
        if q["question"] not in seen:
            seen.add(q["question"]); uniq.append(q)
    json.dump(uniq, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(f"候选 {len(uniq)} 题，type分布: {dict(Counter(q['type'] for q in uniq))}", flush=True)
    print(f"已写 {OUT}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
