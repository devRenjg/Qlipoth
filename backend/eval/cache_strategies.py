"""把 golden_set 100 题的 LLM 搜索策略抓下来缓存（Task6 调研用，非生产）。

为什么：内网 LLM 代理间歇 502，把"调 LLM"与"检索打分"解耦后，策略只抓一次缓存，
之后所有检索方法离线复用 → 零 LLM、秒级、可反复调参，且重跑能续传未完成的题。

带 502 退避重试 + 续传：已在缓存里的题跳过，只补缺的，直到 100 题全齐。

用法:
    py -3.12 eval/cache_strategies.py            # 抓/补齐
    py -3.12 eval/cache_strategies.py --force    # 全部重抓
输出: eval/strategy_cache.json  {id: {question, keywords, file_pattern}}
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import generate_search_strategy  # noqa: E402

GOLDEN = Path(__file__).parent / "golden_set.json"
CACHE = Path(__file__).parent / "strategy_cache.json"


async def _fetch_one(q, retries=6):
    """抓单题策略，502/网络错退避重试（1,2,4,8,16,32s + 题内）。"""
    qid, question = q["id"], q["question"]
    for attempt in range(retries):
        try:
            strategy, elapsed = await generate_search_strategy(question)
            return qid, {
                "question": question,
                "keywords": strategy.get("keywords", [question]),
                "file_pattern": strategy.get("file_pattern", "*"),
                "elapsed": round(elapsed, 2),
            }
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                return qid, {"question": question, "error": str(e)[:120]}
            await asyncio.sleep(min(2 ** attempt, 30))
    return qid, {"question": question, "error": "unreachable"}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--concurrency", type=int, default=2)
    args = ap.parse_args()

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cache = {}
    if CACHE.exists() and not args.force:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    todo = [q for q in golden
            if str(q["id"]) not in cache or "error" in cache.get(str(q["id"]), {})]
    print(f"总 {len(golden)} 题，已缓存 {len(golden)-len(todo)} 题，待抓 {len(todo)} 题")
    if not todo:
        print("全部已缓存。")
        return

    sem = asyncio.Semaphore(args.concurrency)
    done = [0]

    async def _one(q):
        async with sem:
            qid, data = await _fetch_one(q)
            cache[str(qid)] = data
            done[0] += 1
            tag = "ERR" if "error" in data else "ok "
            print(f"[{done[0]}/{len(todo)}] {tag} #{qid} {q['question'][:30]}", flush=True)
            # 每抓 10 题落盘一次，防中途丢
            if done[0] % 10 == 0:
                CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    t0 = time.perf_counter()
    await asyncio.gather(*[_one(q) for q in todo])
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    errs = [k for k, v in cache.items() if "error" in v]
    print(f"\n完成 {time.perf_counter()-t0:.1f}s，缓存 {len(cache)} 题，失败 {len(errs)}")
    if errs:
        print(f"失败题 id: {errs}（重跑本脚本会自动补）")


if __name__ == "__main__":
    asyncio.run(main())
