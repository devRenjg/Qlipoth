"""整体评测：47 篇 API 导入文档(新, 带LLM表格摘要) vs Playwright 旧文档。
搜索效率(答题耗时) + 搜索质量(新旧问答对比)。15 题，文本+表格混合。完全离线。
"""
import asyncio
import os
import sys
import json
import re
import time
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import searcher
import llm
from config import load_settings
from routes.query import _assemble_context

NEW = os.path.join(tempfile.gettempdir(), "eval47_new")
OLD = os.path.join(tempfile.gettempdir(), "eval47_old")
MODEL = load_settings().llm_model


def _docs():
    return sorted(os.listdir(NEW))


async def gen_questions():
    # 从新库随机取一批文档(文本+表格)出题
    files = _docs()
    import random
    random.seed(42)
    pick = random.sample(files, min(16, len(files)))
    blocks = []
    for f in pick:
        t = open(os.path.join(NEW, f), encoding="utf-8", errors="replace").read()
        t = t.split("<!-- TABLE_SUMMARY_START -->")[0]  # 不泄露摘要
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
        blocks.append(f"【{f}】\n{t[:1100]}")
    sysp = ("下面是知识库若干文档内容。拟 15 个有代表性的问答评测问题，覆盖：具体数据(数值/时间/指标)、"
            "故障原因、处置措施、表格信息、负责人/状态。问题答案要能从文档中找到，具体明确。"
            "返回严格JSON:{\"questions\":[...]}（恰好15个，只输出JSON）")
    txt, _ = await llm.chat_completion(
        [{"role": "system", "content": sysp}, {"role": "user", "content": "\n\n".join(blocks)[:26000]}],
        temperature=0.3, model=MODEL)
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    raw = json.loads(txt)["questions"][:15]
    # 健壮：问题可能是 dict({"question": "..."}) 或字符串，统一转字符串
    out = []
    for q in raw:
        if isinstance(q, str):
            out.append(q)
        elif isinstance(q, dict):
            v = q.get("question") or q.get("q") or q.get("text") or ""
            if v:
                out.append(str(v))
    return out


async def answer(q, kb):
    searcher._get_kb_dir = lambda: kb
    t0 = time.time()
    st, _ = await llm.generate_search_strategy(q)
    res = searcher.grep_search(st.get("keywords", [q]), st.get("file_pattern", "*"))
    ctx, fl, _ = _assemble_context(res, q) if res else ("未找到相关内容。", [], [])
    ans, _ = await llm.generate_answer(q, ctx, model=MODEL)
    return {"answer": ans, "elapsed": round(time.time() - t0, 1), "files": len(fl)}


JUDGE = ("问答质量评委。同一问题分别用「新库(API导入)」和「旧库(Playwright)」内容回答。"
         "判断哪个更准确完整。返回严格JSON:{\"winner\":\"new\"/\"old\"/\"tie\",\"new_score\":1-10,"
         "\"old_score\":1-10,\"new_answerable\":true/false,\"old_answerable\":true/false,\"reason\":\"一句话\"}。只输出JSON。")


async def judge(q, n, o):
    user = f"问题：{q}\n\n【新库答案】\n{n[:1400]}\n\n【旧库答案】\n{o[:1400]}"
    txt, _ = await llm.chat_completion(
        [{"role": "system", "content": JUDGE}, {"role": "user", "content": user}], temperature=0, model=MODEL)
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except Exception:
        return {"winner": "?", "new_score": 0, "old_score": 0, "reason": "解析失败"}


async def main():
    print(f"① 出题(覆盖47篇)...", flush=True)
    qs = await gen_questions()
    for i, q in enumerate(qs):
        print(f"  Q{i+1}: {q[:46]}", flush=True)
    res = []
    for i, q in enumerate(qs):
        print(f"② {i+1}/{len(qs)}...", flush=True)
        n = await answer(q, NEW)
        o = await answer(q, OLD)
        j = await judge(q, n["answer"], o["answer"])
        res.append({"q": q, "new": n, "old": o, "judge": j})
        print(f"   {j['winner']} 新{j.get('new_score')}/旧{j.get('old_score')} 答出:新{j.get('new_answerable')}/旧{j.get('old_answerable')} 耗时新{n['elapsed']}/旧{o['elapsed']}", flush=True)
    out = os.path.join(os.environ.get("TEMP", "/tmp"), "eval47_report.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    wc = Counter(r["judge"]["winner"] for r in res)
    na = sum(1 for r in res if r["judge"].get("new_answerable"))
    oa = sum(1 for r in res if r["judge"].get("old_answerable"))
    ns = sum(r["judge"].get("new_score", 0) for r in res)
    os_ = sum(r["judge"].get("old_score", 0) for r in res)
    ne = round(sum(r["new"]["elapsed"] for r in res) / max(len(res), 1), 1)
    oe = round(sum(r["old"]["elapsed"] for r in res) / max(len(res), 1), 1)
    n_tot = len(res)
    print("\n=== 47篇 整体评测 (新API vs 旧Playwright) ===", flush=True)
    print(f"  题数: {n_tot}", flush=True)
    print(f"  搜索质量: 新库胜 {wc.get('new',0)} / 旧库胜 {wc.get('old',0)} / 平 {wc.get('tie',0)}", flush=True)
    print(f"  能答出: 新库 {na}/{n_tot}  旧库 {oa}/{n_tot}", flush=True)
    print(f"  评委总分: 新库 {ns} / 旧库 {os_}", flush=True)
    print(f"  搜索效率(平均答题耗时): 新库 {ne}s / 旧库 {oe}s", flush=True)
    print(f"  报告: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
