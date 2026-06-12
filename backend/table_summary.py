"""P0 表格行级摘要：给已重导文档的规整横排表格生成"列名：值"自然语言行摘要，
追加到文档末尾的专用区块，提升表格数据的检索命中率与 LLM 可理解性。

- 只处理列数对齐的标准横排表（表头+分隔符+等列数据行）；竖排/列数不齐的跳过，避免错位假数据。
- 摘要包在 `<!-- TABLE_SUMMARY_START -->...<!-- TABLE_SUMMARY_END -->` 之间，可识别、
  可重复运行（先删旧块再生成），幂等。
- 既可独立批量跑存量（默认 has_old_version=1 的文档），也可被导入管线调用 append_table_summaries(md)。

用法:
  py -3.12 table_summary.py --dry-run         # 看会处理多少篇、生成多少行摘要，不写盘
  py -3.12 table_summary.py --apply           # 对存量已重导文档写入
  py -3.12 table_summary.py --apply --only "文件名片段"
"""
import os
import re
import sys

KB = os.path.join(os.path.dirname(__file__), "knowledge_base")
_START = "<!-- TABLE_SUMMARY_START -->"
_END = "<!-- TABLE_SUMMARY_END -->"


def _row_summaries(md: str) -> list[str]:
    """对标准横排表格(表头+分隔符+等列数据行)生成行级摘要 '列名：值；...'。"""
    lines = md.splitlines()
    out = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if (re.match(r"^\s*\|.+\|.+\|", l) and i + 1 < len(lines)
                and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]) and "---" in lines[i + 1]):
            headers = [c.strip() for c in l.strip().strip("|").split("|")]
            ncol = len(headers)
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|") and "---" not in lines[j]:
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                if len(cells) == ncol and any(cells):
                    pairs = [f"{headers[k]}：{cells[k]}" for k in range(ncol) if cells[k] and headers[k]]
                    if len(pairs) >= 2:
                        out.append("；".join(pairs))
                j += 1
            i = j
        else:
            i += 1
    return out


def _strip_existing(md: str) -> str:
    """移除已存在的摘要块，保证幂等。"""
    return re.sub(re.escape(_START) + r".*?" + re.escape(_END) + r"\n?", "", md, flags=re.S).rstrip()


def append_table_summaries(md: str) -> tuple[str, int]:
    """给 md 追加表格行摘要块（幂等）。返回 (新md, 摘要行数)。供导入管线调用。"""
    base = _strip_existing(md)
    summaries = _row_summaries(base)
    if not summaries:
        return base, 0
    block = [_START, "", "## 表格数据摘要（便于检索）", ""]
    block += [f"- {s}" for s in summaries]
    block += ["", _END]
    return base + "\n\n" + "\n".join(block) + "\n", len(summaries)


_TABLE_RE = re.compile(r"(?:^[ \t]*\|.*\n?)+", re.M)
_LLM_SYS = """下面是从企业微信文档导出的表格内容，结构可能错乱（单元格被拆成多行、竖排、列对不齐）。
请理解每张表的真实行列含义，为每一条数据记录生成一句自然语言摘要，把该行所有「字段＝值」串成一句话
（例：资源X的mod资源池feOffline、资源名activity_xxx、大小4.31MB、负责人@张三）。
要求：① 一条记录一行 ② 不遗漏关键字段（名称/数值/负责人/状态/时间等）③ 只输出摘要句，不要表头说明、不要解释、不要markdown。"""


async def append_table_summaries_llm(md: str, max_table_chars: int = 12000) -> tuple[str, int]:
    """用 LLM 理解表格（含错乱竖排表）生成结构化行摘要，追加到专用块（幂等）。
    返回 (新md, 摘要行数)。无表格则原样返回。供导入管线/存量补做调用。
    """
    import llm
    from config import load_settings
    base = _strip_existing(md)
    table_lines = [l for l in base.splitlines() if l.lstrip().startswith("|")]
    if len(table_lines) < 2:
        return base, 0
    material = "\n".join(table_lines)[:max_table_chars]
    try:
        txt, _ = await llm.chat_completion(
            [{"role": "system", "content": _LLM_SYS}, {"role": "user", "content": material}],
            temperature=0, model=load_settings().llm_model)
    except Exception:
        return base, 0
    lines = [l.strip().lstrip("-•·").strip() for l in txt.splitlines() if l.strip()]
    lines = [l for l in lines if len(l) > 4 and "｜" not in l and not l.startswith("|")]
    if not lines:
        return base, 0
    block = [_START, "", "## 表格数据摘要（便于检索）", ""]
    block += [f"- {s}" for s in lines]
    block += ["", _END]
    return base + "\n\n" + "\n".join(block) + "\n", len(lines)


def main():
    import sqlite3
    import asyncio
    from database import DB_PATH
    args = sys.argv[1:]
    apply = "--apply" in args
    use_llm = "--llm" in args
    only = ""
    for i, a in enumerate(args):
        if a == "--only" and i + 1 < len(args):
            only = args[i + 1]

    db = sqlite3.connect(DB_PATH)
    files = [r[0] for r in db.execute("SELECT stored_path FROM documents WHERE has_old_version=1").fetchall()]
    db.close()
    if only:
        files = [f for f in files if only in f]

    async def run():
        total_docs, total_rows, changed = 0, 0, 0
        for f in files:
            p = os.path.join(KB, f)
            if not os.path.exists(p):
                continue
            total_docs += 1
            md = open(p, encoding="utf-8", errors="replace").read()
            if use_llm:
                new_md, n = await append_table_summaries_llm(md)
            else:
                new_md, n = append_table_summaries(md)
            if n > 0:
                total_rows += n
                if new_md != md:
                    changed += 1
                    if apply:
                        open(p, "w", encoding="utf-8").write(new_md)
                    print(f"  [{total_docs}/{len(files)}] {f[:34]} → {n} 条摘要", flush=True)
        mode = ("LLM-" if use_llm else "规则-") + ("APPLY(写盘)" if apply else "DRY-RUN(只读)")
        print(f"\n[{mode}] 候选 {len(files)} 篇", flush=True)
        print(f"  处理 {total_docs} 篇，生成摘要共 {total_rows} 条，{'已写入' if apply else '将写入'} {changed} 篇", flush=True)

    asyncio.run(run())


if __name__ == "__main__":
    main()
