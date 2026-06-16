"""客观评测 v2：基于"标准答案事实点"的检索质量评测(103篇 新vs旧)。

针对原评委偏好篇幅/叙事的问题，改造为：
- 出题时让 LLM 同时给出该题的【关键事实点 golden】(基于原文，可客观核对)
- 评委只核对答案命中了几个关键事实点(命中率)，错误信息扣分，不因啰嗦/完整给分
- 检索质量(事实命中率) 与 效率(耗时) 分开报告
5维度分层、全103篇、盲评。完全离线。
"""
import asyncio
import os
import sys
import json
import re
import time
import random
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import searcher
import llm
from config import load_settings
from routes.query import _assemble_context

NEW = os.path.join(tempfile.gettempdir(), "eval103_new")
OLD = os.path.join(tempfile.gettempdir(), "eval103_old")
MODEL = load_settings().llm_model

DIMS = [
    ("精确数据查询", "答案是具体数值/QPS/带宽/时间/大小/比例等精确数据"),
    ("故障原因与处置", "问某故障/告警的原因、影响或当时的处置措施"),
    ("表格结构化数据", "答案在表格里、需定位某行某列的字段值(资源名/负责人/状态/参数)"),
    ("跨文档归纳综合", "需要综合一篇文档多处或概括性信息才能回答"),
    ("方案流程理解", "问某方案/SOP/流程的步骤、设计要点或规则"),
]


def _docs():
    return sorted(os.listdir(OLD))


def _cat(name):
    if re.search(r"压测|告警|限流", name): return "压测告警"
    if re.search(r"复盘|总结", name): return "复盘总结"
    if re.search(r"方案|设计|架构|SOP|预案", name): return "方案设计"
    if re.search(r"清单|名单|列表|rundown|RUNDOWN|RunDown", name): return "清单名单"
    if re.search(r"需求|玩法|功能", name): return "需求玩法"
    return "其他"


def _sample_docs():
    files = _docs()
    by_cat = {}
    for f in files:
        by_cat.setdefault(_cat(f), []).append(f)
    random.seed(2026)
    picked = []
    for cat, fs in by_cat.items():
        random.shuffle(fs)
        picked += fs[:max(2, len(fs) // 6)]
    random.shuffle(picked)
    return picked[:16]


def _text(path, n=1800):
    t = open(path, encoding="utf-8", errors="replace").read()
    t = t.split("<!-- TABLE_SUMMARY_START -->")[0]
    return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)[:n]


async def gen_questions():
    """出题 + 同时生成每题的关键事实点(golden)，基于原文，客观可核对。"""
    docs = _sample_docs()
    blocks = []
    for f in docs:
        op = os.path.join(OLD, f)
        if os.path.exists(op):
            blocks.append(f"【{f}】\n{_text(op)}")
    dim_desc = "\n".join(f"{i+1}. {d[0]}：{d[1]}" for i, d in enumerate(DIMS))
    sysp = (f"下面是知识库若干文档内容。按这 5 个维度各出 4 题(共20)：\n{dim_desc}\n"
            f"每题须给出【关键事实点 facts】：回答该题必须命中的客观要点(数值/名称/结论等)，"
            f"2-5个、来自原文、可逐条核对。要求：问题客观可判定、覆盖不同文档。"
            f'返回严格JSON:{{"questions":[{{"q":"问题","dim":"维度名","facts":["事实点1","事实点2"]}}]}}，'
            f"恰好20个，只输出JSON。")
    txt, _ = await llm.chat_completion(
        [{"role": "system", "content": sysp}, {"role": "user", "content": "\n\n".join(blocks)[:28000]}],
        temperature=0.4, model=MODEL)
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    raw = json.loads(txt)["questions"]
    out = []
    for it in raw[:20]:
        if isinstance(it, dict) and it.get("q") and it.get("facts"):
            out.append({"q": str(it["q"]), "dim": it.get("dim", ""),
                        "facts": [str(x) for x in it["facts"]]})
    return out


def _norm_kws(strategy, q):
    raw = strategy.get("keywords", [q])
    kws = []
    for k in (raw if isinstance(raw, list) else [raw]):
        if isinstance(k, str): kws.append(k)
        elif isinstance(k, dict): kws.append(str(k.get("keyword") or k.get("word") or ""))
        elif isinstance(k, (list, tuple)): kws += [str(x) for x in k]
    return [k for k in kws if k.strip()] or [q]


async def answer(q, kb):
    searcher._get_kb_dir = lambda: kb
    t0 = time.time()
    st, _ = await llm.generate_search_strategy(q)
    res = searcher.grep_search(_norm_kws(st, q), st.get("file_pattern", "*"))
    ctx, fl, _ = _assemble_context(res, q) if res else ("未找到相关内容。", [], [])
    ans, _ = await llm.generate_answer(q, ctx, model=MODEL)
    return {"answer": ans, "elapsed": round(time.time() - t0, 1)}


# 客观评委：只核对事实点命中，不看文采/完整/篇幅；错误信息要扣分
JUDGE = ("你是严格的事实核对评委。给你一个问题、它的【关键事实点清单】、以及两个答案A和B。"
         "请【只】核对每个答案命中了多少个关键事实点(命中=该事实点的核心信息正确出现)，"
         "并判断有无【事实错误】(与事实点矛盾或编造)。\n"
         "评分规则(务必客观)：①只按事实点命中数与正确性，不因答案更长/更详细/更流畅而加分 "
         "②含事实错误或编造要明确指出并降分 ③答非所问/未找到=命中0。\n"
         "返回严格JSON:{\"facts_total\":N,\"A_hit\":命中数,\"B_hit\":命中数,"
         "\"A_wrong\":有无事实错误true/false,\"B_wrong\":true/false,"
         "\"winner\":\"A\"/\"B\"/\"tie\"(命中多且无错者胜,命中相同看有无错误),\"reason\":\"一句话\"}。只输出JSON。")


async def judge(q, facts, a, b):
    facts_str = "\n".join(f"- {x}" for x in facts)
    user = f"问题：{q}\n\n关键事实点：\n{facts_str}\n\n【答案A】\n{a[:1400]}\n\n【答案B】\n{b[:1400]}"
    txt, _ = await llm.chat_completion(
        [{"role": "system", "content": JUDGE}, {"role": "user", "content": user}], temperature=0, model=MODEL)
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except Exception:
        return {"facts_total": len(facts), "A_hit": 0, "B_hit": 0, "winner": "?", "reason": "解析失败"}


async def main():
    print("① 出题+生成关键事实点(golden)...", flush=True)
    qs = await gen_questions()
    from collections import Counter
    print(f"  共{len(qs)}题，维度: {dict(Counter(x['dim'] for x in qs))}", flush=True)

    res = []
    for i, x in enumerate(qs):
        q = x["q"]
        new_is_A = random.random() < 0.5
        na = await answer(q, NEW)
        oa = await answer(q, OLD)
        a_ans, b_ans = (na, oa) if new_is_A else (oa, na)
        j = await judge(q, x["facts"], a_ans["answer"], b_ans["answer"])
        ft = j.get("facts_total") or len(x["facts"])
        a_hit, b_hit = j.get("A_hit", 0), j.get("B_hit", 0)
        a_wrong, b_wrong = j.get("A_wrong", False), j.get("B_wrong", False)
        new_hit, old_hit = (a_hit, b_hit) if new_is_A else (b_hit, a_hit)
        new_wrong, old_wrong = (a_wrong, b_wrong) if new_is_A else (b_wrong, a_wrong)
        w = j.get("winner")
        if w in ("A", "B"):
            winner = "new" if ((w == "A") == new_is_A) else "old"
        else:
            winner = "tie"
        res.append({"q": q, "dim": x["dim"], "facts_total": ft,
                    "new_hit": new_hit, "old_hit": old_hit,
                    "new_wrong": new_wrong, "old_wrong": old_wrong, "winner": winner,
                    "new_elapsed": na["elapsed"], "old_elapsed": oa["elapsed"], "reason": j.get("reason", "")})
        print(f"② {i+1}/{len(qs)}[{x['dim']}] 命中 新{new_hit}/{ft} 旧{old_hit}/{ft} 错:新{new_wrong}/旧{old_wrong} 胜:{winner}", flush=True)

    out = os.path.join(os.environ.get("TEMP", "/tmp"), "eval_objective_report.json")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    n = len(res)
    nf = sum(r["facts_total"] for r in res)
    nh = sum(r["new_hit"] for r in res); oh = sum(r["old_hit"] for r in res)
    nw = sum(1 for r in res if r["new_wrong"]); ow = sum(1 for r in res if r["old_wrong"])
    wc = Counter(r["winner"] for r in res)
    ne = round(sum(r["new_elapsed"] for r in res) / n, 1); oe = round(sum(r["old_elapsed"] for r in res) / n, 1)
    print("\n=== 客观评测(事实命中率) 103篇 新vs旧 ===", flush=True)
    print(f"题数{n} | 总事实点{nf}", flush=True)
    print(f"【检索质量】事实命中率: 新 {nh}/{nf}({100*nh//max(nf,1)}%) vs 旧 {oh}/{nf}({100*oh//max(nf,1)}%)", flush=True)
    print(f"事实错误数: 新 {nw} 题 / 旧 {ow} 题", flush=True)
    print(f"逐题胜负: 新胜{wc.get('new',0)}/旧胜{wc.get('old',0)}/平{wc.get('tie',0)}", flush=True)
    print(f"【检索效率】平均耗时: 新 {ne}s / 旧 {oe}s", flush=True)
    print("\n--- 分维度事实命中率(新 vs 旧) ---", flush=True)
    for d, _ in DIMS:
        sub = [r for r in res if r["dim"] == d]
        if not sub: continue
        sf = sum(r["facts_total"] for r in sub)
        snh = sum(r["new_hit"] for r in sub); soh = sum(r["old_hit"] for r in sub)
        print(f"  {d}({len(sub)}题): 新 {snh}/{sf} vs 旧 {soh}/{sf}", flush=True)
    print(f"\n报告: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
