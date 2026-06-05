"""一次性脚本：扫描知识库文档，用 LLM 多标签分类，写入 document_tags。

用法：
  python auto_tag.py --sample 10      # 抽样 N 篇，只打印结果不写库（验收用）
  python auto_tag.py --limit 50       # 处理前 N 篇并写库
  python auto_tag.py                  # 全量处理并写库
  python auto_tag.py --only-untagged  # 仅处理当前无任何标签的文档
"""
import argparse
import asyncio
import json
import sqlite3
import sys

from config import load_settings
from llm import chat_completion
from searcher import read_file_content

CONTENT_LIMIT = 6000  # 每篇送入 LLM 的最大字符数，控制 token 成本
CONCURRENCY = 4       # 并发请求数

CLASSIFY_SYSTEM = """你是知识库文档分类助手。给定一篇文档内容，从下面固定的标签集合中选出所有**确实贴合**该文档主题的标签，可多选，也可不选（宁缺毋滥，只在明显相关时才选）。

标签集合及定义：
- 业务需求：该文档主要描述一个业务功能或产品需求
- 高可用保障：技术方案文档，面向高可用保障，如直播PCU、降级、限流、架构演进、压测、容灾等
- 成本：与带宽、采购、服务器成本和预算相关，含技术/业务预算与第三方采购项
- 安全：涉及审核、风控、技术安全、业务安全相关领域
- 直播体验：涉及画质、清晰度、延迟、卡顿等播放体验相关的性能说明
- 弹幕：涉及弹幕玩法、弹幕技术方案、弹幕业务需求
- 红包：涉及发放红包的技术方案、业务需求、玩法等

返回严格的 JSON（不要 markdown 代码块）：
{"tags": ["标签1", "标签2"], "reason": "一句话说明依据"}

规则：
- tags 只能取自上面 7 个标签名，逐字匹配，不得新造标签。
- 一篇文档可能命中多个标签（如既是业务需求又涉及红包）。
- 若都不贴合，返回 {"tags": [], "reason": "..."}。"""

VALID_TAGS = {"业务需求", "高可用保障", "成本", "安全", "直播体验", "弹幕", "红包"}


async def classify(name: str, content: str) -> tuple[list[str], str]:
    user = f"文档标题：{name}\n\n文档内容：\n{content[:CONTENT_LIMIT]}"
    try:
        text, _ = await chat_completion(
            [
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
    except Exception as e:  # noqa: BLE001
        return [], f"LLM错误: {e}"
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], f"解析失败: {text[:80]}"
    tags = [t for t in data.get("tags", []) if t in VALID_TAGS]
    return tags, data.get("reason", "")


def fetch_docs(db, limit, only_untagged):
    sql = "SELECT id, original_name, stored_path FROM documents"
    if only_untagged:
        sql += " WHERE id NOT IN (SELECT DISTINCT document_id FROM document_tags)"
    sql += " ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.execute(sql).fetchall()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="抽样N篇仅打印不写库")
    ap.add_argument("--limit", type=int, default=0, help="处理前N篇")
    ap.add_argument("--only-untagged", action="store_true", help="仅处理无标签文档")
    args = ap.parse_args()

    db_path = load_settings().db_path
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row

    name_to_id = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM tags")}
    missing = VALID_TAGS - set(name_to_id)
    if missing:
        print(f"缺少标签，请先 seed：{missing}", file=sys.stderr)
        return

    dry_run = args.sample > 0
    limit = args.sample or args.limit
    docs = fetch_docs(db, limit, args.only_untagged)
    print(f"待处理 {len(docs)} 篇，dry_run={dry_run}，并发={CONCURRENCY}", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY)
    counts = {t: 0 for t in VALID_TAGS}
    done = 0

    async def worker(doc):
        nonlocal done
        async with sem:
            content = read_file_content(doc["stored_path"])
            if not content:
                print(f"[skip] #{doc['id']} 无内容 {doc['original_name'][:40]}", flush=True)
                return
            tags, reason = await classify(doc["original_name"], content)
            done += 1
            print(f"[{done}/{len(docs)}] #{doc['id']} {doc['original_name'][:42]}", flush=True)
            print(f"    -> {tags}  ({reason[:60]})", flush=True)
            if not dry_run and tags:
                for t in tags:
                    db.execute(
                        "INSERT OR IGNORE INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                        (doc["id"], name_to_id[t]),
                    )
                db.commit()
            for t in tags:
                counts[t] += 1

    await asyncio.gather(*(worker(d) for d in docs))

    print("\n=== 标签命中统计 ===", flush=True)
    for t in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {t}: {counts[t]}", flush=True)
    if dry_run:
        print("\n(dry-run 未写库)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
