"""LLM 反向出题:抽样知识库文档,让 LLM 生成"这篇文档能回答的问题",
自动建立 问题 -> 来源文档 的配对,产出候选黄金集 golden_set.json。

用法:
    py -3.12 eval/generate_questions.py --sample 8          # 抽样 8 篇,快速验证
    py -3.12 eval/generate_questions.py --sample 50         # 放量
    py -3.12 eval/generate_questions.py --sample 50 --per-doc 2 --concurrency 4

输出:eval/golden_set.json(候选,需人工抽检后再用于评测)
人工裁决:删掉不像真实提问的条目即可;relevant_files 已自动填好。
"""
import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from config import load_settings          # noqa: E402
from llm import chat_completion           # noqa: E402
from eval.golden_meta import enrich, era_of, tags_of  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_set.json"

QUESTION_TYPES = ["数量类", "负责人类", "方案类", "排查类", "歧义类"]

GEN_PROMPT = """你在帮一个"大型直播活动保障知识库"构建搜索评测集。

下面是知识库里某一篇文档的内容(可能被截断)。请站在真实使用者(直播保障团队的研发/产品/测试同事)的角度,
生成 {per_doc} 个他们**可能真实会问、且答案确实在这篇文档里**的问题。

要求:
- 模仿同事口语化提问,简短自然,像在工作群里发问,不要文档腔
- 问题必须能用这篇文档的内容回答,不要编造文档里没有的内容
- 覆盖不同问题类型,每个问题标注一个 type,从这些里选:{types}
- 不要在问题里直接抄文档标题或大段原文
- 避免过于宽泛、任何文档都能答的问题(如"这个项目是做什么的")

只返回 JSON 数组,不要 markdown 代码块标记,格式:
[
  {{"question": "...", "type": "数量类"}},
  {{"question": "...", "type": "排查类"}}
]

文档标题:{title}
文档内容:
{content}
"""

MAX_DOC_CHARS = 8000  # 每篇喂给 LLM 的最大字符,控制 token 成本


def _list_docs() -> list[Path]:
    kb = Path(load_settings().knowledge_base_dir)
    return sorted(kb.glob("*.md"))


def _filter_docs(docs: list[Path], era: str = "", tag: str = "") -> list[Path]:
    """按活动期 / 标签筛选候选文档池（定向补题用）。"""
    out = docs
    if era:
        out = [d for d in out if era_of(d.name) == era]
    if tag:
        out = [d for d in out if tag in tags_of(d.name)]
    return out


def _sample_docs(docs: list[Path], n: int, seed: int) -> list[Path]:
    """按大小分层抽样:大/中/小文档都覆盖到,避免只抽到大文档。"""
    if n >= len(docs):
        return docs
    rnd = random.Random(seed)
    by_size = sorted(docs, key=lambda p: p.stat().st_size)
    # 分 3 层等量抽
    third = len(by_size) // 3
    layers = [by_size[:third], by_size[third:2 * third], by_size[2 * third:]]
    per_layer = max(1, n // 3)
    picked: list[Path] = []
    for layer in layers:
        picked.extend(rnd.sample(layer, min(per_layer, len(layer))))
    # 补齐
    if len(picked) < n:
        rest = [d for d in docs if d not in picked]
        picked.extend(rnd.sample(rest, min(n - len(picked), len(rest))))
    return picked[:n]


def _strip_fence(text: str) -> str:
    text = text.strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
    return text.strip()


async def _gen_for_doc(doc: Path, per_doc: int, sem: asyncio.Semaphore) -> list[dict]:
    content = doc.read_text(encoding="utf-8")[:MAX_DOC_CHARS]
    prompt = GEN_PROMPT.format(
        per_doc=per_doc,
        types="、".join(QUESTION_TYPES),
        title=doc.stem,
        content=content,
    )
    async with sem:
        try:
            text, _ = await chat_completion(
                [{"role": "user", "content": prompt}], temperature=0.4
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {doc.name}: {e}", flush=True)
            return []
    try:
        items = json.loads(_strip_fence(text))
    except json.JSONDecodeError:
        print(f"  [SKIP] {doc.name}: LLM 返回非 JSON", flush=True)
        return []
    out = []
    for it in items:
        q = (it.get("question") or "").strip()
        if not q:
            continue
        qtype = it.get("type", "").strip()
        if qtype not in QUESTION_TYPES:
            qtype = "方案类"
        out.append({"question": q, "type": qtype, "relevant_files": [doc.name]})
    print(f"  [OK]  {doc.name} -> {len(out)} 题", flush=True)
    return out

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=8, help="抽样文档数")
    ap.add_argument("--per-doc", type=int, default=2, help="每篇出题数")
    ap.add_argument("--concurrency", type=int, default=4, help="并发数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--append", action="store_true", help="追加到已有 golden_set")
    ap.add_argument("--era", default="", help="只从该活动期文档出题(如 S15/赛事)")
    ap.add_argument("--tag", default="", help="只从含该标签的文档出题(如 直播体验)")
    ap.add_argument("--types", default="", help="逗号分隔限定出题类型(如 方案类,歧义类)")
    args = ap.parse_args()

    if args.types:
        want = [t.strip() for t in args.types.split(",") if t.strip()]
        globals()["QUESTION_TYPES"] = [t for t in want if t in QUESTION_TYPES] or QUESTION_TYPES

    docs = _list_docs()
    pool = _filter_docs(docs, args.era, args.tag)
    print(f"知识库文档总数: {len(docs)}；筛选后候选池: {len(pool)}"
          f"（era={args.era or '全部'} tag={args.tag or '全部'}）")
    picked = _sample_docs(pool, args.sample, args.seed)
    print(f"本次抽样: {len(picked)} 篇,每篇 {args.per_doc} 题,并发 {args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)
    tasks = [_gen_for_doc(d, args.per_doc, sem) for d in picked]
    results = await asyncio.gather(*tasks)

    questions: list[dict] = []
    for r in results:
        questions.extend(r)

    # 新题回填 tags/era 元数据
    for q in questions:
        meta = enrich(q.get("relevant_files", []))
        q["tags"] = meta["tags"]
        q["era"] = meta["era"]

    if args.append and GOLDEN_PATH.exists():
        existing = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
        seen = {q["question"] for q in existing}
        # 旧题若缺 tags/era 也补齐
        for q in existing:
            if "tags" not in q or "era" not in q:
                meta = enrich(q.get("relevant_files", []))
                q.setdefault("tags", meta["tags"])
                q.setdefault("era", meta["era"])
        questions = existing + [q for q in questions if q["question"] not in seen]

    # 编号
    for i, q in enumerate(questions, 1):
        q.pop("id", None)  # 去掉旧 id 重排
        ordered = {"id": i, "question": q["question"], "type": q["type"],
                   "relevant_files": q["relevant_files"],
                   "tags": q.get("tags", []), "era": q.get("era", "其他/通用")}
        questions[i - 1] = ordered

    GOLDEN_PATH.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写入 {GOLDEN_PATH}")
    print(f"共 {len(questions)} 题。请人工抽检:删掉不像真实提问的,确认 relevant_files。")
    from collections import Counter
    by_type = Counter(q["type"] for q in questions)
    print("分类型分布:", dict(by_type))


if __name__ == "__main__":
    asyncio.run(main())
