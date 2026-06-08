"""给全库文档打"大型活动"维度标签：S赛 / 跨晚 / 春晚（可多标）。

与10个主题标签是不同维度（一篇文档可同时有主题标签+活动标签）。
策略（规则+LLM混合，用户已确认）：
- 规则打明确的：S15/S赛/LPL/lol/英雄联盟/竞猜/峡谷/S14/S13 → S赛；
  文件名含"春晚" → 春晚；含"跨晚/跨年晚会/跨年" → 跨晚。
- CNY/活动B/活动A等笼统词、无活动关键词、或需细分跨晚vs春晚的 → LLM读正文判断（可多选）。
- 完全判不出活动归属的文档：不打活动标签（不强加）。

用法:
  py -3.12 tag_activities.py            # dry-run 打印
  py -3.12 tag_activities.py --write     # 写库
"""
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
import llm
from config import load_settings

DB = load_settings().db_path
KB = Path(load_settings().knowledge_base_dir)
CONTENT_LIMIT = 5000
ACT_TAGS = ["S赛", "跨晚", "春晚"]

# 规则：明确命中即可直接判定
RE_SSAI = re.compile(r"S1[345]|S赛|LPL|lol|英雄联盟|竞猜|峡谷", re.I)
RE_CHUNWAN = re.compile(r"春晚")
RE_KUAWAN = re.compile(r"跨晚|跨年晚会|跨年")
# 笼统/需LLM细分的信号
RE_CNY_GENERIC = re.compile(r"CNY|cny|活动B|活动A|春节|红包|过年")

LLM_SYS = """你在给"大型直播活动保障知识库"的文档打**活动维度**标签。
这些文档围绕 B 站几个大型活动展开，请判断该文档主要服务于哪个/哪些活动，可多选也可不选：
- S赛：英雄联盟全球总决赛及相关赛事（S15/S14、LPL、英雄联盟、竞猜答题、赛事直播）
- 跨晚：跨年晚会（12月31日跨年）
- 春晚：春节联欢晚会（除夕）及春节档（CNY、活动B、活动A、春节红包等）

注意：CNY 春节档项目常同时包含"跨年晚会"和"除夕春晚"两场，若文档同时涉及两者，跨晚和春晚都选。

返回严格 JSON（不要 markdown）：{"activities": ["春晚"], "reason": "一句话依据"}
- activities 只能取自 S赛/跨晚/春晚，逐字匹配，可为空数组（确实判不出活动归属时）。"""


def rule_tag(name: str):
    """文件名规则。返回 (确定的活动集, 是否需要LLM细分)。"""
    acts = set()
    if RE_SSAI.search(name):
        acts.add("S赛")
    if RE_CHUNWAN.search(name):
        acts.add("春晚")
    if RE_KUAWAN.search(name):
        acts.add("跨晚")
    # 命中笼统CNY词但没明确春晚/跨晚 → 需LLM细分
    need_llm = bool(RE_CNY_GENERIC.search(name)) and not ({"春晚", "跨晚"} & acts)
    # 完全没命中任何活动信号 → 需LLM兜底判断
    if not acts and not RE_CNY_GENERIC.search(name):
        need_llm = True
    return acts, need_llm


async def llm_tag(name, content):
    user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
    try:
        txt, _ = await llm.chat_completion(
            [{"role": "system", "content": LLM_SYS}, {"role": "user", "content": user}],
            temperature=0)
    except Exception as e:  # noqa: BLE001
        return [], f"err:{e}"
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        d = json.loads(txt)
        return [a for a in d.get("activities", []) if a in ACT_TAGS], d.get("reason", "")
    except json.JSONDecodeError:
        return [], "parse fail"


async def main():
    write = "--write" in sys.argv
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    tagid = {r["name"]: r["id"] for r in con.execute("SELECT id,name FROM tags").fetchall()}
    for t in ACT_TAGS:
        assert t in tagid, f"标签 {t} 未建，请先建标签"

    docs = con.execute("SELECT id, original_name, stored_path FROM documents").fetchall()
    print(f"{'[写库]' if write else '[DRY-RUN]'} 扫描 {len(docs)} 篇\n")

    rule_done = {}
    llm_queue = []
    for d in docs:
        acts, need_llm = rule_tag(d["original_name"])
        if need_llm:
            llm_queue.append((d["id"], d["original_name"], d["stored_path"], acts))
        else:
            rule_done[d["id"]] = acts
    print(f"规则直接判定: {len(rule_done)} 篇；需 LLM 细分/兜底: {len(llm_queue)} 篇", flush=True)

    sem = asyncio.Semaphore(4)
    llm_result = {}

    async def _one(did, name, sp, seed_acts):
        async with sem:
            p = KB / sp
            content = p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""
            acts, reason = await llm_tag(name, content)
            llm_result[did] = (set(seed_acts) | set(acts), reason)

    await asyncio.gather(*[_one(d, n, sp, a) for d, n, sp, a in llm_queue])

    final = dict(rule_done)
    final.update({did: acts for did, (acts, _) in llm_result.items()})
    from collections import Counter
    cnt, none_cnt = Counter(), 0
    for acts in final.values():
        if not acts:
            none_cnt += 1
        for a in acts:
            cnt[a] += 1
    print("\n=== 活动标签分布 ===")
    for a in ACT_TAGS:
        print(f"  {a}: {cnt[a]}")
    print(f"  无活动标签: {none_cnt}")

    name_by_id = {d["id"]: d["original_name"] for d in docs}
    print("\nLLM 判定样例(前12):")
    for did, (acts, reason) in list(llm_result.items())[:12]:
        print(f"  {name_by_id[did][:28]:<28} -> {sorted(acts) or '(无)'} | {reason[:28]}")

    if write:
        applied = 0
        for did, acts in final.items():
            for a in acts:
                con.execute("INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?,?)",
                            (did, tagid[a]))
                applied += 1
        con.commit()
        print(f"\n已写入 {applied} 条活动标签关联")
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
