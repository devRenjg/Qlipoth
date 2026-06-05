"""回答段提速横评（Task8）：固定检索上下文，横比 haiku/sonnet/opus 的【耗时×质量】。

profile 显示回答 LLM 占 21s（端到端大头），全程用 opus-4-8。本脚本对同一批题、
同一份检索上下文，分别用三个模型生成回答，测：
  - 延迟：total（完整回答）+ ttfb（流式首字，用户感知）
  - 质量：复用 sim_personas 的 LLM judge（opus 当裁判）4 维打分

检索上下文固定走"策略缓存→grep→fuse"（与模型无关，公平），只变回答模型。

用法: py -3.12 eval/answer_speed_eval.py --n 15
输出: eval/reports/ans_speed_<时间戳>.md (+ .json)
"""
import argparse
import asyncio
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

import llm                                       # noqa: E402
from searcher import grep_search                 # noqa: E402
from routes.query import _assemble_context       # noqa: E402
from config import load_settings                 # noqa: E402
from eval.sim_personas import JUDGE_SYSTEM, _parse_json, _avg_score  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
CACHE_PATH = Path(__file__).parent / "strategy_cache.json"
REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))
MODELS = ["claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-8"]
SHORT = {"claude-haiku-4-5-20251001": "haiku", "claude-sonnet-4-6": "sonnet", "claude-opus-4-8": "opus"}


def _sample(golden, n):
    from collections import defaultdict
    by = defaultdict(list)
    for q in golden:
        by[q.get("type", "")].append(q)
    out, types, i = [], list(by), 0
    while len(out) < n and any(by.values()):
        t = types[i % len(types)]
        if by[t]:
            out.append(by[t].pop(0))
        i += 1
    return out[:n]


async def _answer_with_model(prompt: str, model: str) -> tuple[str, float, float]:
    """用指定模型流式生成回答。返回 (answer, ttfb_s, total_s)。直接构造 anthropic 请求覆盖模型。"""
    import httpx
    settings = load_settings()
    base = settings.llm_base_url.rstrip("/")
    msgs = [{"role": "user", "content": prompt}]
    url, headers, payload = llm._build_anthropic_request(settings, msgs, 0.3, base)
    payload["model"] = model
    payload["stream"] = True
    parts, ttfb, t0 = [], None, time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                ds = line[6:]
                if ds == "[DONE]":
                    break
                try:
                    d = json.loads(ds)
                    txt = llm._extract_stream_delta(d, "anthropic")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if txt:
                    if ttfb is None:
                        ttfb = time.perf_counter() - t0
                    parts.append(txt)
    return "".join(parts), ttfb or 0.0, time.perf_counter() - t0


async def _judge(question: str, answer: str) -> dict:
    """复用 sim_personas 的裁判口径，opus 当裁判。"""
    try:
        content, _ = await llm.chat_completion(
            [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": f"【用户问题】{question}\n\n【系统答案】{answer}"}],
            temperature=0)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    return _parse_json(content) or {"error": "judge parse fail"}


def _build_prompt(question, search_text):
    return llm.ANSWER_PROMPT.format(soul=llm.SOUL, search_results=search_text, question=question)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15, help="0=全部100题")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--models", default="", help="逗号分隔短名(haiku,sonnet,opus)，空=全部")
    args = ap.parse_args()

    global MODELS
    if args.models:
        want = {m.strip() for m in args.models.split(",")}
        MODELS = [m for m in MODELS if SHORT[m] in want]

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    sample = golden if args.n == 0 else _sample(golden, args.n)
    print(f"回答段横评 {len(sample)} 题 × {len(MODELS)} 模型: {[SHORT[m] for m in MODELS]}", flush=True)

    # 预备：每题固定检索上下文（与模型无关）
    items = []
    for q in sample:
        c = cache.get(str(q["id"]))
        if not c or "error" in c:
            continue
        results = grep_search(c["keywords"], c.get("file_pattern", "*"))
        search_text, _, _ = _assemble_context(results, q["question"]) if results else ("未找到", [], [])
        items.append({"q": q, "prompt": _build_prompt(q["question"], search_text),
                      "ctx": len(search_text)})
    print(f"  {len(items)} 题上下文就绪，平均 {sum(i['ctx'] for i in items)//max(len(items),1)} 字", flush=True)

    sem = asyncio.Semaphore(args.concurrency)

    async def _gen(item, model):
        async with sem:
            q = item["q"]
            try:
                ans, ttfb, total = await _answer_with_model(item["prompt"], model)
                verdict = await _judge(q["question"], ans)
                avg = _avg_score(verdict) if "error" not in verdict else 0.0
                print(f"  #{q['id']:<3} {SHORT[model]:<7} ttfb={ttfb:4.1f}s total={total:5.1f}s "
                      f"质量={avg:.1f} chars={len(ans)}", flush=True)
                return {"id": q["id"], "type": q.get("type", ""), "model": model,
                        "ttfb": ttfb, "total": total, "chars": len(ans),
                        "avg": avg, "verdict": verdict}
            except Exception as e:  # noqa: BLE001
                print(f"  #{q['id']} {SHORT[model]} ERROR: {e}", flush=True)
                return {"id": q["id"], "model": model, "error": str(e)}

    tasks = [_gen(item, m) for item in items for m in MODELS]
    t0 = time.perf_counter()
    rows = await asyncio.gather(*tasks)
    wall = time.perf_counter() - t0

    # 按模型聚合
    def agg(model):
        rs = [r for r in rows if r["model"] == model and "error" not in r]
        if not rs:
            return {"n": 0}
        ttfb = [r["ttfb"] for r in rs]
        total = [r["total"] for r in rs]
        avg = [r["avg"] for r in rs if r["avg"] > 0]
        return {"n": len(rs),
                "ttfb_mean": round(sum(ttfb) / len(ttfb), 2),
                "total_mean": round(sum(total) / len(total), 2),
                "total_p90": round(sorted(total)[min(len(total) - 1, int(0.9 * (len(total) - 1)))], 2),
                "quality_mean": round(sum(avg) / len(avg), 2) if avg else 0,
                "chars_mean": round(sum(r["chars"] for r in rs) / len(rs))}

    aggs = {m: agg(m) for m in MODELS}
    print(f"\n=== 回答段 模型横评 ({len(items)}题, wall={wall:.0f}s) ===")
    print(f"{'模型':<8}{'TTFB':>8}{'Total':>8}{'p90':>8}{'质量(0-5)':>12}{'字数':>8}")
    for m in MODELS:
        a = aggs[m]
        print(f"{SHORT[m]:<8}{a.get('ttfb_mean',0):>8}{a.get('total_mean',0):>8}"
              f"{a.get('total_p90',0):>8}{a.get('quality_mean',0):>12}{a.get('chars_mean',0):>8}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    rep = {"timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
           "n": len(items), "wall_s": round(wall, 1), "models": MODELS,
           "short": SHORT, "aggs": aggs, "rows": rows}
    (REPORTS_DIR / f"ans_speed_{ts}.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"ans_speed_{ts}.md", rep)
    print(f"\n报告: eval/reports/ans_speed_{ts}.md (+ .json)")


def _write_md(path, rep):
    lines = [f"# 回答段模型横评 {rep['timestamp']}", "",
             f"- {rep['n']} 题，固定检索上下文，仅变回答模型；质量为 opus 裁判 4 维均分(0-5)",
             "", "| 模型 | TTFB(s) | Total(s) | Total p90(s) | 质量(0-5) | 字数 |",
             "|------|---------|----------|--------------|-----------|------|"]
    for m in rep["models"]:
        a = rep["aggs"][m]
        lines.append(f"| {rep['short'][m]} | {a.get('ttfb_mean',0)} | {a.get('total_mean',0)} | "
                     f"{a.get('total_p90',0)} | {a.get('quality_mean',0)} | {a.get('chars_mean',0)} |")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
