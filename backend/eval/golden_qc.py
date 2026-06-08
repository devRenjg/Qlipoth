"""Golden Set 新题质检：LLM 判定每道题能否被其 relevant_file 真实回答。

LLM 反向出题可能产出"答案不在文档里/太宽泛"的题。本脚本对指定 id 范围的题，
把 question + 文档正文喂给 judge，判定 answerable(能答) / off(跑题) / vague(太宽泛)。
dry-run 打印，--prune 删除不合格题并重排号。
"""
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
import llm
from config import load_settings

GOLDEN = Path(__file__).parent / "golden_set.json"
KB = Path(load_settings().knowledge_base_dir)

JUDGE = """你在质检知识库搜索评测题。给你一道【问题】和它标注的【来源文档正文】。
判断：这道问题是否能**主要依据这篇文档**得到明确回答，且不是任何文档都能答的宽泛问题。

返回严格 JSON（不要 markdown）：
{"verdict": "answerable" | "off" | "vague", "reason": "一句话"}
- answerable: 文档里有这道题的答案，问题具体
- off: 文档里没有答案/答非所问
- vague: 太宽泛，多数文档都能答（如"这个项目做什么"）"""


async def judge(q, content):
    user = f"【问题】{q}\n\n【来源文档正文】\n{content[:6000]}"
    try:
        txt, _ = await llm.chat_completion(
            [{"role": "system", "content": JUDGE}, {"role": "user", "content": user}],
            temperature=0)
    except Exception as e:  # noqa: BLE001
        return {"verdict": "error", "reason": str(e)}
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        return {"verdict": "error", "reason": "parse fail"}


async def main():
    prune = "--prune" in sys.argv
    min_id = 101
    for a in sys.argv:
        if a.startswith("--min-id="):
            min_id = int(a.split("=")[1])
    g = json.load(open(GOLDEN, encoding="utf-8"))
    targets = [q for q in g if q["id"] >= min_id]
    print(f"质检 id>={min_id} 的 {len(targets)} 题（共 {len(g)} 题）\n")

    sem = asyncio.Semaphore(4)

    async def one(q):
        async with sem:
            f = q["relevant_files"][0]
            p = KB / f
            if not p.exists():
                return q["id"], {"verdict": "off", "reason": "文件不存在"}
            content = p.read_text(encoding="utf-8", errors="ignore")
            return q["id"], await judge(q["question"], content)

    verdicts = dict(await asyncio.gather(*[one(q) for q in targets]))

    bad = []
    from collections import Counter
    cnt = Counter()
    for q in targets:
        v = verdicts[q["id"]]
        cnt[v["verdict"]] += 1
        if v["verdict"] in ("off", "vague"):
            bad.append(q["id"])
            print(f"  ✗ #{q['id']} [{v['verdict']}] {q['question'][:36]}  | {v['reason'][:40]}")
    print(f"\n判定汇总: {dict(cnt)}  不合格 {len(bad)} 题")

    if prune and bad:
        kept = [q for q in g if q["id"] not in bad]
        for i, q in enumerate(kept, 1):
            q["id"] = i
        json.dump(kept, open(GOLDEN, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"已删除 {len(bad)} 题，剩 {len(kept)} 题并重排号")


if __name__ == "__main__":
    asyncio.run(main())