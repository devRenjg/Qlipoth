"""问题分类器验证（方案2 第一步）：规则分类 vs haiku 分类，对标 golden set 5 类标注。

方案2(分类路由)成立的前提是分类够准。本脚本用 golden set 自带的 type 当
ground truth，测两种分类器的准确率与混淆矩阵，选定上线用哪种。

分类目的（路由用）：把问题分到"简单事实类(数量/负责人)"vs"复杂分析类(方案/排查/歧义)"
其实路由只需要二分（简单→快模型/精简context，复杂→强模型/全context），
但先测细粒度 5 类准确率，再看二分准确率（路由真正关心的）。

用法: py -3.12 eval/classifier_eval.py [--haiku]
输出: eval/reports/classifier_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import llm  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))
HAIKU = "claude-haiku-4-5-20251001"

TYPES = ["数量类", "负责人类", "排查类", "方案类", "歧义类"]
# 路由二分：简单事实类走快路径，复杂类走强路径
SIMPLE = {"数量类", "负责人类"}


def classify_rule(q: str) -> str:
    """规则分类（零成本）。按关键词信号判类，优先级从强到弱。"""
    # 负责人类：谁/负责/跟进/对接/找谁
    if re.search(r'谁|负责|跟进|对接|是谁|找谁|owner|负责人', q, re.I):
        return "负责人类"
    # 数量类：几/多少/多久/比例/数字+量词
    if re.search(r'几个|几台|多少|多久|几次|几条|比例|占比|总共|一共|多大|几种|人数', q):
        return "数量类"
    # 歧义类：到底/还是/区别/矛盾/算不算/哪个
    if re.search(r'到底|还是|区别|矛盾|算不算|哪个是|是.+还是|有点', q):
        return "歧义类"
    # 方案类：怎么搞/怎么做/能不能/如何/为什么能
    if re.search(r'怎么搞|怎么做|怎么弄|如何|能不能|可以.*吗|为什么能|是不是有', q):
        return "方案类"
    # 兜底：排查类（最大类）
    return "排查类"


CLS_PROMPT = """你是问题分类器。把用户问题分到下面5类之一，只返回类名两个字+"类"，不要别的：
- 数量类：问数字/数量/比例/时长（几个、多少、多久、占比）
- 负责人类：问谁负责/谁跟进/找谁/负责人是谁
- 排查类：查具体事实/状态/配置/父文档/原因（是不是、有没有、为什么触发）
- 方案类：问怎么做/如何配置/能不能实现/方案设计
- 歧义类：对概念有困惑、二选一、文档矛盾（到底、还是、区别、算不算）
只输出类名，如：数量类"""


async def classify_haiku(q: str) -> str:
    """haiku 分类，返回 5 类之一；解析失败兜底排查类。"""
    try:
        content, _ = await llm.chat_completion(
            [{"role": "system", "content": CLS_PROMPT}, {"role": "user", "content": q}],
            temperature=0)
    except Exception:  # noqa: BLE001
        return "排查类"
    for t in TYPES:
        if t in content:
            return t
    return "排查类"


async def _haiku_call(q):
    """用 haiku 模型分类（覆盖 chat_completion 默认模型）。"""
    import httpx
    from config import load_settings
    s = load_settings()
    base = s.llm_base_url.rstrip("/")
    msgs = [{"role": "system", "content": CLS_PROMPT}, {"role": "user", "content": q}]
    url, headers, payload = llm._build_anthropic_request(s, msgs, 0, base)
    payload["model"] = HAIKU
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8)) as c:
            r = await c.post(url, json=payload, headers=headers)
        txt = llm._extract_response_text(r.json(), "anthropic")
    except Exception:  # noqa: BLE001
        return "排查类", time.perf_counter() - t0
    for t in TYPES:
        if t in txt:
            return t, time.perf_counter() - t0
    return "排查类", time.perf_counter() - t0


def _accuracy(rows, key):
    """5类准确率 + 路由二分准确率（简单 vs 复杂）。"""
    n = len(rows)
    fine = sum(1 for r in rows if r[key] == r["gold"])
    # 二分：预测的简单/复杂 与 真实的简单/复杂 是否一致
    binary = sum(1 for r in rows
                 if (r[key] in SIMPLE) == (r["gold"] in SIMPLE))
    return {"n": n, "fine_acc": round(fine / n, 4), "binary_acc": round(binary / n, 4)}


def _confusion(rows, key):
    """混淆矩阵 gold→pred 计数。"""
    cm = defaultdict(lambda: defaultdict(int))
    for r in rows:
        cm[r["gold"]][r[key]] += 1
    return {g: dict(cm[g]) for g in TYPES}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--haiku", action="store_true", help="同时测 haiku 分类(走LLM)")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    rows = []
    for q in golden:
        rows.append({"id": q["id"], "q": q["question"], "gold": q["type"],
                     "rule": classify_rule(q["question"])})

    haiku_lat = []
    if args.haiku:
        sem = asyncio.Semaphore(args.concurrency)
        async def _one(r):
            async with sem:
                cls, lat = await _haiku_call(r["q"])
                r["haiku"] = cls
                haiku_lat.append(lat)
        await asyncio.gather(*[_one(r) for r in rows])

    rule_acc = _accuracy(rows, "rule")
    print(f"=== 问题分类器验证 ({len(rows)}题, golden type 当标准) ===\n")
    print(f"规则分类   5类准确率={rule_acc['fine_acc']}  路由二分准确率={rule_acc['binary_acc']}  延迟≈0")
    result = {"rule": rule_acc, "rule_confusion": _confusion(rows, "rule")}
    if args.haiku:
        haiku_acc = _accuracy(rows, "haiku")
        lat_mean = round(sum(haiku_lat) / len(haiku_lat), 2) if haiku_lat else 0
        print(f"haiku分类   5类准确率={haiku_acc['fine_acc']}  路由二分准确率={haiku_acc['binary_acc']}  延迟均值={lat_mean}s")
        result["haiku"] = haiku_acc
        result["haiku_confusion"] = _confusion(rows, "haiku")
        result["haiku_lat_mean"] = lat_mean

    # 混淆矩阵打印（规则）
    print("\n规则分类混淆矩阵 (行=真实, 列=预测):")
    print(f"{'真实\\预测':<10}" + "".join(f"{t:>8}" for t in TYPES))
    cm = result["rule_confusion"]
    for g in TYPES:
        print(f"{g:<10}" + "".join(f"{cm[g].get(p,0):>8}" for p in TYPES))

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "n": len(rows), "types": TYPES, "simple": sorted(SIMPLE),
           "result": result, "rows": rows}
    (REPORTS_DIR / f"classifier_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"classifier_{ts}.md", rep)
    print(f"\n报告: eval/reports/classifier_{ts}.md (+ .json)")


def _write_md(path, rep):
    r = rep["result"]
    lines = [f"# 问题分类器验证 {rep['timestamp']}", "",
             f"- {rep['n']} 题，golden set 的 type 标注当标准答案",
             f"- 路由二分：简单事实类{rep['simple']} 走快路径，其余走强路径",
             "", "## 准确率", "",
             "| 分类器 | 5类准确率 | 路由二分准确率 | 延迟 |",
             "|--------|-----------|----------------|------|",
             f"| 规则(零成本) | {r['rule']['fine_acc']} | {r['rule']['binary_acc']} | ~0 |"]
    if "haiku" in r:
        lines.append(f"| haiku(LLM) | {r['haiku']['fine_acc']} | {r['haiku']['binary_acc']} | {r.get('haiku_lat_mean',0)}s |")
    lines += ["", "## 规则分类混淆矩阵 (行=真实, 列=预测)", "",
              "| 真实\\预测 | " + " | ".join(TYPES) + " |",
              "|" + "---|" * (len(TYPES) + 1)]
    cm = r["rule_confusion"]
    for g in TYPES:
        lines.append(f"| {g} | " + " | ".join(str(cm[g].get(p, 0)) for p in TYPES) + " |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
