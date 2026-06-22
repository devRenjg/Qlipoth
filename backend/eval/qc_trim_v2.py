# -*- coding: utf-8 -*-
"""质检 golden_v2 候选(LLM判定答案是否真在relevant_files里)，过滤后按目标分布裁剪到100题。"""
import asyncio, json, sys, re
from pathlib import Path
from collections import defaultdict

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from llm import chat_completion  # noqa

KB = BACKEND_DIR / "knowledge_base"
CAND = Path(__file__).parent / "golden_v2_candidates.json"
OUT = Path(__file__).parent / "golden_set.json"

# 目标分布(共100)
TARGET = {"数量类": 11, "排查类": 20, "负责人类": 12, "方案类": 12, "歧义类": 0,
          "表格数据类": 17, "跨文档综合类": 13, "精确数值类": 15}

def read_doc(name, limit=3500):
    p = KB / name
    return p.read_text(encoding="utf-8", errors="replace")[:limit] if p.exists() else ""

QC_PROMPT = """判断这道评测题能否用给定文档真实回答。
题目：{q}
文档内容：
{body}
返回严格JSON：{{"ok": true/false, "reason":"一句话"}}。ok=true表示文档里确有答案且问题具体；
答非所问/太宽泛/答案不在文档里则false。只输出JSON。"""

def strip_fence(t):
    t = t.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n?", "", t); t = re.sub(r"\n?```$", "", t)
    return t.strip()

async def qc_one(q, sem):
    async with sem:
        # 跨文档题：拼接首个文档即可粗检(主要验证非编造)
        body = "\n\n".join(read_doc(f, 1800) for f in q["relevant_files"][:2])
        try:
            txt, _ = await chat_completion(
                [{"role": "user", "content": QC_PROMPT.format(q=q["question"], body=body)}], temperature=0)
            r = json.loads(strip_fence(txt))
            return q, bool(r.get("ok"))
        except Exception:
            return q, False

async def main():
    cand = json.load(open(CAND, encoding="utf-8"))
    sem = asyncio.Semaphore(6)
    results = await asyncio.gather(*[qc_one(q, sem) for q in cand])
    passed = [q for q, ok in results if ok]
    print(f"质检：{len(cand)} 候选 → {len(passed)} 通过", flush=True)

    # 按type裁剪到目标分布
    by_type = defaultdict(list)
    for q in passed:
        by_type[q["type"]].append(q)
    final = []
    for t, n in TARGET.items():
        picks = by_type.get(t, [])[:n]
        final.extend(picks)
    # 不足100则用剩余补齐
    if len(final) < 100:
        used = {id(q) for q in final}
        pool = [q for q in passed if id(q) not in used]
        final.extend(pool[:100 - len(final)])
    final = final[:100]
    # 重新编号
    for i, q in enumerate(final, 1):
        q["id"] = i
    json.dump(final, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(f"最终 {len(final)} 题，type分布: {dict(Counter(q['type'] for q in final))}", flush=True)
    print(f"era分布: {dict(Counter(q['era'] for q in final))}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
