"""Task4: 模拟 user_a/user_b/user_c 三位用户的多轮真实提问 + 追问，端到端打知识库。

不走离线检索，而是 HTTP 打生产 /api/query/stream（和浏览器完全一致）：
每轮生成 conversation_id → 流式拿答案 → 存 chat_history（让下一轮触发指代消解追问）。

每轮答案用 LLM 裁判按 4 维打分（相关性/准确性/完整性/是否答非所问），
汇总成评测报告，并把"答非所问/明显错误"的弱项明细列出来，供修复。

用法:
    py -3.12 eval/sim_personas.py                  # 跑全部 persona
    py -3.12 eval/sim_personas.py --tag pre        # 报告打标签
    py -3.12 eval/sim_personas.py --base http://localhost:8000

输出: eval/reports/sim_<时间戳>_<tag>.md (+ .json)
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from llm import chat_completion  # noqa: E402

REPORTS_DIR = Path(__file__).parent / "reports"
_BJ = timezone(timedelta(hours=8))

# 三位示例用户 → DB users（user_c=admin, user_b=super, user_a=user）
PERSONAS = {
    "user_a": {"user_id": 3, "role": "user"},
    "user_b": {"user_id": 2, "role": "super"},
    "user_c": {"user_id": 1, "role": "admin"},
}

JUDGE_SYSTEM = """你是知识库问答质量裁判。给定【用户问题】【系统答案】，按下面 4 个维度打分（0-5 整数），并判定是否"答非所问"。

评分维度：
- relevance 相关性：答案是否在回应这个问题（而非跑题）
- accuracy 准确性：答案中的事实/数字/结论是否可信、无明显编造
- completeness 完整性：是否覆盖了问题的关键点
- groundedness 有据性：答案是否基于检索到的知识库内容（而非空泛套话/拒答）

特别判定：
- off_topic=true 表示答非所问或完全没回应问题（如"未找到相关内容"但其实该问题知识库应该有，或回答了别的东西）
- refused=true 表示系统明确表示无法回答/无相关内容

返回严格 JSON（不要 markdown 代码块）：
{"relevance":4,"accuracy":4,"completeness":3,"groundedness":4,"off_topic":false,"refused":false,"comment":"一句话点评，指出问题所在"}"""


def _parse_json(text: str) -> dict | None:
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# 多轮对话脚本：每个示例用户一组（合成 demo 主题，与真实业务无关）。
# 每组首问 + 若干追问（追问故意用指代："那""这个""他们"以压测 coref）。
CONVERSATIONS = {
    "user_a": [
        [
            "示例活动的值班安排是怎样的，多少人？",
            "那高峰时段集中在哪几天？",
            "这些人主要负责什么？",
        ],
        [
            "示例奖池是怎么拆分的？",
            "那发放上有没有做降级或限流？",
        ],
    ],
    "user_b": [
        [
            "示例消息审核链路是怎么设计的？",
            "它的安全防护具体有哪些手段？",
            "那如果审核被绕过有没有兜底？",
        ],
        [
            "示例压测方案的目标和结论是什么？",
            "压测中暴露了哪些瓶颈？",
        ],
    ],
    "user_c": [
        [
            "示例需求上线后的覆盖率多少，对比上次如何？",
            "覆盖率不达标的话有什么补救措施？",
        ],
        [
            "示例预演覆盖了哪些场景？",
            "负责人是谁？",
            "他们预演里发现的主要问题是什么？",
        ],
    ],
}


async def _stream_query(client: httpx.AsyncClient, base: str, question: str,
                        conv_id: str) -> dict:
    """打 /api/query/stream，收完整答案 + meta。返回 {answer, meta, elapsed}。"""
    payload = {"question": question, "conversation_id": conv_id, "tag_ids": []}
    answer_parts: list[str] = []
    meta: dict = {}
    t0 = time.perf_counter()
    async with client.stream("POST", f"{base}/api/query/stream", json=payload,
                             timeout=180) as resp:
        resp.raise_for_status()
        buffer = ""
        async for chunk in resp.aiter_text():
            buffer += chunk
            lines = buffer.split("\n")
            buffer = lines.pop()
            for line in lines:
                if not line.startswith("data: "):
                    continue
                try:
                    msg = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if msg["type"] == "meta":
                    meta = msg["data"]
                elif msg["type"] == "chunk":
                    answer_parts.append(msg["data"])
                elif msg["type"] == "error":
                    answer_parts.append(f"[ERROR] {msg['data']}")
    return {
        "answer": "".join(answer_parts),
        "meta": meta,
        "elapsed": round(time.perf_counter() - t0, 2),
    }


async def _save_history(client: httpx.AsyncClient, base: str, persona: str,
                        question: str, answer: str, conv_id: str, source_urls: list):
    """存 chat_history，使下一轮追问能载入历史触发 coref。"""
    payload = {
        "question": question,
        "answer": answer,
        "source_urls": source_urls,
        "user_id": PERSONAS[persona]["user_id"],
        "conversation_id": conv_id,
        "selected_tags": [],
    }
    try:
        await client.post(f"{base}/api/chat/history", json=payload, timeout=30)
    except Exception:  # noqa: BLE001
        pass


async def _judge(question: str, answer: str) -> dict:
    """LLM 裁判打分。失败返回降级标记。"""
    user = f"【用户问题】{question}\n\n【系统答案】\n{answer[:4000]}"
    try:
        text, _ = await chat_completion(
            [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    data = _parse_json(text) or {}
    return data


async def _run_conversation(client, base, persona, turns, conv_idx) -> list[dict]:
    """跑一个会话的所有轮次（顺序，追问依赖前一轮已入库的历史）。"""
    conv_id = str(uuid.uuid4())
    rows = []
    for turn_idx, question in enumerate(turns):
        try:
            res = await _stream_query(client, base, question, conv_id)
        except Exception as e:  # noqa: BLE001
            rows.append({"persona": persona, "conv": conv_idx, "turn": turn_idx,
                         "question": question, "error": str(e)})
            continue
        answer = res["answer"]
        meta = res["meta"]
        await _save_history(client, base, persona, question, answer, conv_id,
                            meta.get("source_urls", []))
        verdict = await _judge(question, answer)
        rows.append({
            "persona": persona,
            "conv": conv_idx,
            "turn": turn_idx,
            "question": question,
            "is_followup": meta.get("is_followup", False),
            "resolved_question": meta.get("resolved_question", question),
            "results_count": meta.get("search_results_count", 0),
            "elapsed": res["elapsed"],
            "answer_preview": answer[:200],
            "answer_len": len(answer),
            "verdict": verdict,
        })
        print(f"  [{persona}] 会话{conv_idx} 轮{turn_idx} "
              f"({'追问' if meta.get('is_followup') else '首问'}) "
              f"{res['elapsed']}s 命中{meta.get('search_results_count',0)} "
              f"→ {_score_str(verdict)}")
    return rows


def _score_str(v: dict) -> str:
    if "error" in v:
        return f"裁判失败 {v['error'][:30]}"
    flags = []
    if v.get("off_topic"):
        flags.append("答非所问")
    if v.get("refused"):
        flags.append("拒答")
    avg = _avg_score(v)
    return f"均分{avg:.1f}{' ['+','.join(flags)+']' if flags else ''}"


def _avg_score(v: dict) -> float:
    keys = ("relevance", "accuracy", "completeness", "groundedness")
    vals = [v.get(k) for k in keys if isinstance(v.get(k), (int, float))]
    return sum(vals) / len(vals) if vals else 0.0


def _aggregate(rows: list[dict]) -> dict:
    valid = [r for r in rows if "error" not in r and "error" not in r.get("verdict", {})]
    if not valid:
        return {"n": 0}
    dims = ("relevance", "accuracy", "completeness", "groundedness")
    agg = {d: 0.0 for d in dims}
    off_topic = refused = 0
    for r in valid:
        v = r["verdict"]
        for d in dims:
            agg[d] += v.get(d, 0) or 0
        if v.get("off_topic"):
            off_topic += 1
        if v.get("refused"):
            refused += 1
    n = len(valid)
    out = {d: round(agg[d] / n, 3) for d in dims}
    out["avg"] = round(sum(out[d] for d in dims) / len(dims), 3)
    out["n"] = n
    out["off_topic"] = off_topic
    out["refused"] = refused
    out["avg_elapsed"] = round(sum(r["elapsed"] for r in valid) / n, 2)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    print(f"模拟三位用户多轮提问 → {args.base}")
    all_rows: list[dict] = []
    t_start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        for persona, convs in CONVERSATIONS.items():
            for ci, turns in enumerate(convs):
                rows = await _run_conversation(client, args.base, persona, turns, ci)
                all_rows.extend(rows)
    total_time = time.perf_counter() - t_start

    overall = _aggregate(all_rows)
    by_persona = {p: _aggregate([r for r in all_rows if r["persona"] == p])
                  for p in CONVERSATIONS}

    print(f"\n=== 总体 ===  题数 {overall.get('n',0)}  均分 {overall.get('avg',0)}  "
          f"答非所问 {overall.get('off_topic',0)}  拒答 {overall.get('refused',0)}  "
          f"平均耗时 {overall.get('avg_elapsed',0)}s  总耗时 {total_time:.1f}s")
    for p, m in by_persona.items():
        print(f"  {p}: 均分 {m.get('avg',0)} 相关{m.get('relevance',0)} "
              f"准确{m.get('accuracy',0)} 完整{m.get('completeness',0)} "
              f"有据{m.get('groundedness',0)} 答非所问{m.get('off_topic',0)}")

    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    stem = f"sim_{ts}{tag}"
    report = {
        "timestamp": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag,
        "base": args.base,
        "overall": overall,
        "by_persona": by_persona,
        "rows": all_rows,
    }
    (REPORTS_DIR / f"{stem}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_md(REPORTS_DIR / f"{stem}.md", report, total_time)
    print(f"\n报告已写入: eval/reports/{stem}.md (+ .json)")


def _write_md(path: Path, rep: dict, total_time: float):
    o = rep["overall"]
    lines = [
        f"# 多轮模拟问答评测 {rep['timestamp']}",
        "",
        f"- 标签: `{rep['tag'] or '(none)'}`  目标: `{rep['base']}`",
        f"- 总耗时: {total_time:.1f}s",
        "",
        "## 总体",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 题数 | {o.get('n',0)} |",
        f"| 综合均分(0-5) | {o.get('avg',0)} |",
        f"| 相关性 | {o.get('relevance',0)} |",
        f"| 准确性 | {o.get('accuracy',0)} |",
        f"| 完整性 | {o.get('completeness',0)} |",
        f"| 有据性 | {o.get('groundedness',0)} |",
        f"| 答非所问数 | {o.get('off_topic',0)} |",
        f"| 拒答数 | {o.get('refused',0)} |",
        f"| 平均耗时 | {o.get('avg_elapsed',0)}s |",
        "",
        "## 分用户",
        "",
        "| 用户 | 题数 | 均分 | 相关 | 准确 | 完整 | 有据 | 答非所问 | 拒答 |",
        "|------|------|------|------|------|------|------|---------|------|",
    ]
    for p, m in rep["by_persona"].items():
        lines.append(f"| {p} | {m.get('n',0)} | {m.get('avg',0)} | {m.get('relevance',0)} | "
                     f"{m.get('accuracy',0)} | {m.get('completeness',0)} | {m.get('groundedness',0)} | "
                     f"{m.get('off_topic',0)} | {m.get('refused',0)} |")
    lines += ["", "## 弱项明细 (均分<3 或 答非所问/拒答)", ""]
    for r in rep["rows"]:
        if "error" in r:
            lines.append(f"- [{r['persona']}] #{r['conv']}.{r['turn']} {r['question'][:40]} :: ERROR {r['error'][:60]}")
            continue
        v = r.get("verdict", {})
        if "error" in v:
            lines.append(f"- [{r['persona']}] {r['question'][:40]} :: 裁判失败")
            continue
        weak = _avg_score(v) < 3 or v.get("off_topic") or v.get("refused")
        if weak:
            tag2 = []
            if v.get("off_topic"):
                tag2.append("答非所问")
            if v.get("refused"):
                tag2.append("拒答")
            lines.append(f"- [{r['persona']}] {'追问' if r['is_followup'] else '首问'} "
                         f"「{r['question']}」{' '+','.join(tag2) if tag2 else ''}")
            lines.append(f"    - 改写: {r['resolved_question']}")
            lines.append(f"    - 命中文件数: {r['results_count']}  均分: {_avg_score(v):.1f}  点评: {v.get('comment','')}")
            lines.append(f"    - 答案预览: {r['answer_preview']}")
    lines += ["", "## 全部轮次一览", ""]
    for r in rep["rows"]:
        if "error" in r:
            continue
        v = r.get("verdict", {})
        lines.append(f"- [{r['persona']}] {'追' if r['is_followup'] else '首'} 「{r['question'][:36]}」 "
                     f"均分{_avg_score(v):.1f} 命中{r['results_count']} {r['elapsed']}s")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
