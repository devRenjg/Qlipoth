# -*- coding: utf-8 -*-
"""复杂表格汇总摘要补全：对"含汇总段(总计/合计/分布等)但标准摘要未覆盖"的表格文档，
用 LLM 把汇总段提炼成中文摘要，追加到独立标记块，提升"问整体/汇总"类问题的命中率。

- 标准 table_summary 只处理规整横排表；本工具专攻复杂交叉表/竖排汇总表。
- 幂等：摘要包在 <!-- SUMMARY_AGG_START/END --> 块，重跑先删旧块。
- LLM 失败/无汇总段则跳过，绝不破坏原文档。

用法:
  py -3.12 summary_complex_tables.py --dry-run        # 列出候选,不写
  py -3.12 summary_complex_tables.py --apply          # 写入
  py -3.12 summary_complex_tables.py --apply --only "片段"
"""
import os, re, sys, asyncio, sqlite3

BACKEND = os.path.dirname(__file__)
sys.path.insert(0, BACKEND)
KB = os.path.join(BACKEND, "knowledge_base")
from database import DB_PATH
import llm

_START = "<!-- SUMMARY_AGG_START -->"
_END = "<!-- SUMMARY_AGG_END -->"
_SUMWORD = re.compile(r"总计|合计|总共|小计|总人数|人数统计|工种分布|占比|汇总|总和")

_SYS = (
    "下面是一篇文档里包含【汇总/统计数据】的表格片段(可能是多列并排的交叉表或竖排表，"
    "格式较乱)。请把其中的汇总数字提炼成清晰中文要点，每条一行、用自然语言，"
    "覆盖各类总计/分项/分布/占比等关键数字。只提炼表中真实存在的数字，不推算、不编造。"
    "若片段中没有有意义的汇总数字，只回复：无。")


def _strip(md: str) -> str:
    return re.sub(re.escape(_START) + r".*?" + re.escape(_END), "", md, flags=re.S).rstrip() + "\n"


def _agg_segment(md: str) -> str:
    """抽取含汇总词的表格行 + 其上下文，作为 LLM 材料(限长)。"""
    lines = md.splitlines()
    keep = set()
    for i, l in enumerate(lines):
        if _SUMWORD.search(l) and l.lstrip().startswith("|"):
            for j in range(max(0, i - 8), min(len(lines), i + 3)):
                if lines[j].lstrip().startswith("|"):
                    keep.add(j)
    seg = "\n".join(lines[i] for i in sorted(keep))
    return seg[:8000]


def _candidates(only=""):
    db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
    rows = db.execute("SELECT original_name, stored_path FROM documents WHERE has_old_version=1").fetchall()
    db.close()
    out = []
    for r in rows:
        if only and only not in r["stored_path"] and only not in r["original_name"]:
            continue
        p = os.path.join(KB, r["stored_path"])
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8", errors="replace").read()
        if t.count("\n|") < 10:
            continue
        body = _strip(t)
        body = re.sub(r"<!-- TABLE_SUMMARY_START.*?TABLE_SUMMARY_END -->", "", body, flags=re.S)
        sumhits = len(_SUMWORD.findall(body))
        m = re.search(r"TABLE_SUMMARY_START.*?TABLE_SUMMARY_END", t, re.S)
        in_summary = len(_SUMWORD.findall(m.group(0))) if m else 0
        if sumhits >= 2 and in_summary < sumhits * 0.3:
            out.append((r["original_name"], r["stored_path"]))
    return out


async def _process(name, path, sem, apply):
    async with sem:
        p = os.path.join(KB, path)
        t = open(p, encoding="utf-8", errors="replace").read()
        seg = _agg_segment(_strip(t))
        if len(seg) < 20:
            return name, 0
        try:
            txt, _ = await llm.chat_completion(
                [{"role": "system", "content": _SYS}, {"role": "user", "content": seg}], temperature=0)
        except Exception as e:
            print(f"  [跳过-LLM失败] {name[:30]}: {str(e)[:40]}")
            return name, 0
        lines = [l.strip().lstrip("-•·* ").strip() for l in txt.splitlines() if l.strip()]
        lines = [l for l in lines if len(l) > 4 and not l.startswith("|")]
        if not lines or (len(lines) == 1 and lines[0] in ("无", "无。")):
            return name, 0
        block = _START + "\n\n## 汇总数据摘要（便于检索整体/统计类问题）\n\n" + \
                "\n".join(f"- {s}" for s in lines) + "\n\n" + _END
        new_t = _strip(t).rstrip() + "\n\n" + block + "\n"
        if apply:
            open(p, "w", encoding="utf-8").write(new_t)
        return name, len(lines)


async def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    only = ""
    if "--only" in args:
        only = args[args.index("--only") + 1]
    cands = _candidates(only)
    print(f"候选文档: {len(cands)} 篇")
    sem = asyncio.Semaphore(5)
    results = await asyncio.gather(*[_process(n, p, sem, apply) for n, p in cands])
    done = [(n, c) for n, c in results if c > 0]
    total = sum(c for _, c in done)
    print(f"\n[{'APPLY写入' if apply else 'DRY-RUN'}] 生成汇总摘要: {len(done)} 篇 / {total} 行")
    for n, c in sorted(done, key=lambda x: -x[1])[:12]:
        print(f"  {c}行  {n[:40]}")

if __name__ == "__main__":
    asyncio.run(main())
