"""当日重导文档的新旧对比评测：录入质量 + 检索效率 + 检索质量。

读取 .reimport_last_batch.json（当天重导成功的文件名 + 旧版备份目录），对这批文档：
① 录入质量：新旧 正文/图片/表格/乱码 指标对比
② 检索效率/质量：基于新内容出题，新库 vs 旧库各跑问答，LLM评委对比
完全离线，monkeypatch _get_kb_dir，不碰线上。结果汇总打印并落盘到 %TEMP%/eval_daily_report.json。

【每日汇报】本脚本产出是每日微信汇报的第②块，必须并入汇报(用户明确要求，别只报入库数)。
完整汇报口径(四块+待入库--stats)见 migrate_wecom_reimport.py 头部 "DAILY REPORT" 注释块。
"""
import asyncio
import os
import sys
import json
import re
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import searcher
import llm
from config import load_settings
from routes.query import _assemble_context

KB = os.path.join(os.path.dirname(__file__), "knowledge_base")
LAST_BATCH = os.path.join(os.path.dirname(__file__), ".reimport_last_batch.json")
MODEL = load_settings().llm_model
GARBLE = re.compile(r"ra7v24|JZj|\*FF[0-9A-F]{6}|tdkey|MENTION_WXWORK|BB08|wozbKqDg")


def _metrics(t: str) -> dict:
    no_img = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    return {
        "txt": len(re.sub(r"\s", "", no_img)),
        "img": len(re.findall(r"!\[[^\]]*\]\([^)]*\)", t)),
        "tbl": t.count("\n|"),
        "gar": len(GARBLE.findall(t)),
    }


async def gen_questions(new_dir: str, files: list) -> list:
    blocks = []
    for f in files[:12]:
        p = os.path.join(new_dir, f)
        if not os.path.exists(p):
            continue
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", open(p, encoding="utf-8", errors="replace").read())
        blocks.append(f"【{f}】\n{t[:1400]}")
    sysp = ("下面是若干篇文档内容。基于真实内容拟 8 个问答评测问题，覆盖具体数据/故障原因/"
            "处置措施/表格信息。答案要能从文中找到。返回严格JSON:{\"questions\":[...]}（恰好8个，只输出JSON）")

    def _parse(txt: str):
        txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        # 先直接解析;失败则截取第一个 { 到最后一个 } 再解析(去前后杂质)
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            i, j = txt.find("{"), txt.rfind("}")
            if i < 0 or j <= i:
                return None
            try:
                return json.loads(txt[i:j + 1])
            except json.JSONDecodeError:
                return None  # JSON 内部损坏(如中间缺逗号),交给外层重试

    # LLM 偶发返回损坏JSON(前后杂质 or 内部语法错)。重试最多3次重新生成,
    # 仍失败则返回空列表(让调用方跳过本次评测,而非崩掉整个每日任务)。
    for attempt in range(3):
        txt, _ = await llm.chat_completion(
            [{"role": "system", "content": sysp}, {"role": "user", "content": "\n\n".join(blocks)[:24000]}],
            temperature=0.3 + attempt * 0.2, model=MODEL)
        obj = _parse(txt)
        if obj is not None:
            return obj.get("questions", [])[:8]
        print(f"[eval] gen_questions 第{attempt + 1}次JSON解析失败,重试...", flush=True)
    print("[eval] gen_questions 连续3次解析失败,跳过检索质量评测", flush=True)
    return []


async def answer_in_kb(question: str, kb_dir: str) -> dict:
    orig = searcher._get_kb_dir
    searcher._get_kb_dir = lambda: kb_dir
    import time
    t0 = time.time()
    try:
        strategy, _ = await llm.generate_search_strategy(question)
        raw_kws = strategy.get("keywords", [question])
        kws = []
        for k in (raw_kws if isinstance(raw_kws, list) else [raw_kws]):
            if isinstance(k, str):
                kws.append(k)
            elif isinstance(k, dict):
                v = k.get("keyword") or k.get("word") or ""
                if v:
                    kws.append(str(v))
            elif isinstance(k, (list, tuple)):
                kws.extend(str(x) for x in k if isinstance(x, str))
        kws = [k for k in kws if k.strip()] or [question]
        results = searcher.grep_search(kws, strategy.get("file_pattern", "*"))
        if results:
            search_text, files_read, _ = _assemble_context(results, question)
        else:
            search_text, files_read = "未找到相关内容。", []
        answer, _ = await llm.generate_answer(question, search_text, model=MODEL)
        return {"answer": answer, "files": len(files_read), "elapsed": round(time.time() - t0, 1)}
    finally:
        searcher._get_kb_dir = orig


JUDGE = ("问答质量评委。同一问题分别用新库/旧库内容回答。判断哪个更准确完整。返回严格JSON:"
         "{\"winner\":\"new\"/\"old\"/\"tie\",\"new_score\":1-10,\"old_score\":1-10,"
         "\"new_answerable\":true/false,\"old_answerable\":true/false,\"reason\":\"一句话\"}。只输出JSON。")


async def judge(q, n, o):
    user = f"问题：{q}\n\n【新库答案】\n{n[:1400]}\n\n【旧库答案】\n{o[:1400]}"
    txt, _ = await llm.chat_completion(
        [{"role": "system", "content": JUDGE}, {"role": "user", "content": user}], temperature=0, model=MODEL)
    txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(txt)
    except Exception:
        return {"winner": "?", "new_score": 0, "old_score": 0, "reason": "解析失败"}


async def main():
    if not os.path.exists(LAST_BATCH):
        print("无 .reimport_last_batch.json，今日无新增文档，跳过评测", flush=True)
        return
    batch = json.load(open(LAST_BATCH, encoding="utf-8"))
    files = batch.get("files", [])
    old_backup = batch.get("backup_dir", "")
    if not files:
        print("今日新增 0 篇，跳过评测", flush=True)
        return
    print(f"今日新增 {len(files)} 篇，开始新旧对比评测", flush=True)

    # 建临时新/旧目录（仅含当天这批）
    ND = os.path.join(tempfile.gettempdir(), "eval_daily_new")
    OD = os.path.join(tempfile.gettempdir(), "eval_daily_old")
    for d in (ND, OD):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    usable = []
    for f in files:
        np, op = os.path.join(KB, f), os.path.join(old_backup, f)
        if os.path.exists(np) and os.path.exists(op):
            shutil.copy2(np, os.path.join(ND, f))
            shutil.copy2(op, os.path.join(OD, f))
            usable.append(f)

    # ① 录入质量指标汇总
    so = {"txt": 0, "img": 0, "tbl": 0, "gar": 0}
    sn = dict(so)
    for f in usable:
        o = _metrics(open(os.path.join(OD, f), encoding="utf-8", errors="replace").read())
        n = _metrics(open(os.path.join(ND, f), encoding="utf-8", errors="replace").read())
        for k in so:
            so[k] += o[k]
            sn[k] += n[k]

    # ②③ 检索效率/质量
    questions = await gen_questions(ND, usable)
    qres = []
    for q in questions:
        # 单题容错:内网LLM网关偶发流式中断(RemoteProtocolError)等网络抖动,
        # 重试1次;仍失败则跳过该题,不让整个评测崩溃(评测是抽样统计,漏1题不影响结论)。
        for attempt in range(2):
            try:
                nr = await answer_in_kb(q, ND)
                orr = await answer_in_kb(q, OD)
                j = await judge(q, nr["answer"], orr["answer"])
                qres.append({"q": q, "new": nr, "old": orr, "judge": j})
                break
            except Exception as e:
                if attempt == 0:
                    print(f"[eval] 题[{q[:20]}]答题异常({type(e).__name__}),重试...", flush=True)
                else:
                    print(f"[eval] 题[{q[:20]}]重试仍失败,跳过: {type(e).__name__}", flush=True)

    from collections import Counter
    wc = Counter(r["judge"]["winner"] for r in qres)
    na = sum(1 for r in qres if r["judge"].get("new_answerable"))
    oa = sum(1 for r in qres if r["judge"].get("old_answerable"))
    ne = round(sum(r["new"]["elapsed"] for r in qres) / max(len(qres), 1), 1)
    oe = round(sum(r["old"]["elapsed"] for r in qres) / max(len(qres), 1), 1)

    out = os.path.join(os.environ.get("TEMP", "/tmp"), "eval_daily_report.json")
    json.dump({"files": usable, "metrics": {"old": so, "new": sn}, "qa": qres}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n=== 当日重导评测汇总 ===", flush=True)
    print(f"评测文档数: {len(usable)}", flush=True)
    print("【录入质量(旧→新)】", flush=True)
    print(f"  图片 {so['img']}→{sn['img']} | 乱码 {so['gar']}→{sn['gar']} | 表格行 {so['tbl']}→{sn['tbl']} | 正文 {so['txt']}→{sn['txt']}", flush=True)
    print("【检索质量】", flush=True)
    print(f"  问答 {len(qres)} 题: 新库胜{wc.get('new',0)}/旧库胜{wc.get('old',0)}/平{wc.get('tie',0)} | 能答出 新{na}/旧{oa}", flush=True)
    print("【检索效率】", flush=True)
    print(f"  平均答题耗时: 新库{ne}s / 旧库{oe}s", flush=True)
    print(f"报告: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
