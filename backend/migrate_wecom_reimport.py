"""知识库企微文档批量重导（企微 API 替换 Playwright 旧内容）+ 质量门禁。

用法:
  py -3.12 migrate_wecom_reimport.py --dry-run --limit 10      # 只读+对比+出报告，不写盘
  py -3.12 migrate_wecom_reimport.py --dry-run                 # 全部 dry-run
  py -3.12 migrate_wecom_reimport.py --write                   # 全量执行(会备份原文)
  py -3.12 migrate_wecom_reimport.py --write --only "文件名片段" # 只处理匹配的

质量门禁逐篇判定 better / worse / same / skip，只有 better 才在 --write 时替换。
worse/same 默认不替换（worse 标记人工复核）。失败(读取异常)一律 skip，绝不用空盖好内容。
"""
import asyncio
import sys
import os
import re
import json
import shutil
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import wecom
from database import DB_PATH

KB = os.path.join(os.path.dirname(__file__), "knowledge_base")
OLD_DIR = os.path.join(os.path.dirname(__file__), "knowledge_base_old")
_FAIL_FILE = os.path.join(os.path.dirname(__file__), ".reimport_fails.json")
_LAST_BATCH_FILE = os.path.join(os.path.dirname(__file__), ".reimport_last_batch.json")
_BJ = timezone(timedelta(hours=8))
REQ_GAP = 10.0  # 每次读取后的间隔(秒)。配额每天约20份，串行+10s间隔，跑满即停

# protobuf/Playwright 噪声特征
_GARBLE = [r"ra7v24", r"JZj", r"\*FF[0-9A-F]{6}", r"\\tdkey", r"MENTION_WXWORK",
           r"wozbKqDg", r"tdfn", r"normalLink"]
_GARBLE_RE = re.compile("|".join(_GARBLE))


def _src_url(md: str) -> str:
    m = re.search(r"^>\s*来源:\s*(\S+)", md, re.M)
    return m.group(1) if m else ""


def _header(md: str) -> str:
    """取原 md 的头部元信息块（# 标题 + > 来源/负责人/父文档），重导时保留。"""
    lines = md.splitlines()
    out = []
    for ln in lines:
        if ln.startswith("#") or ln.startswith(">") or ln.strip() == "":
            out.append(ln)
            if len(out) > 12:
                break
        else:
            break
    return "\n".join(out).rstrip()


def _metrics(md: str) -> dict:
    """质量指标：纯文本字数(去图片)、图片数、表格行数、乱码特征数。"""
    no_img = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    text_len = len(re.sub(r"\s+", "", no_img))
    imgs = len(re.findall(r"!\[[^\]]*\]\([^)]*\)", md))
    table_rows = len(re.findall(r"^\s*\|.*\|", md, re.M)) + md.count("\n|")
    garble = len(_GARBLE_RE.findall(md))
    return {"text_len": text_len, "imgs": imgs, "tables": table_rows, "garble": garble}


def _judge(old: dict, new: dict) -> tuple[str, str]:
    """对比新旧指标，返回 (verdict, reason)。

    策略（放宽替换门槛）：API 读取成功默认就替换（better），仅在**明显倒退**时拒绝（worse）：
    - 丢图：旧有图(≥3)而新几乎没有（新 < 旧的 30%）
    - 大量丢表格：旧表格多(≥10行)而新骤减到不足 30%
    - 内容近乎全失：新正文 < 旧的 15% 且新无图无表（疑似读取残缺）
    其余一律 better。倒退的标记 worse 供人工复核，不自动覆盖。
    """
    reasons = []
    # 倒退检测
    if old["imgs"] >= 3 and new["imgs"] < old["imgs"] * 0.3:
        reasons.append(f"丢图 {old['imgs']}→{new['imgs']}")
        return "worse", "；".join(reasons)
    if old["tables"] >= 10 and new["tables"] < old["tables"] * 0.3:
        reasons.append(f"表格骤减 {old['tables']}→{new['tables']}")
        return "worse", "；".join(reasons)
    if old["text_len"] > 200 and new["text_len"] < old["text_len"] * 0.15 and new["imgs"] == 0 and new["tables"] < 3:
        reasons.append(f"内容近乎全失 正文{old['text_len']}→{new['text_len']}、无图无表")
        return "worse", "；".join(reasons)
    # 默认替换，附改善亮点（便于报告）
    if new["imgs"] > old["imgs"]:
        reasons.append(f"图片 {old['imgs']}→{new['imgs']}")
    if old["garble"] > 5 and new["garble"] < old["garble"]:
        reasons.append(f"乱码 {old['garble']}→{new['garble']}")
    if new["tables"] > old["tables"]:
        reasons.append(f"表格 {old['tables']}→{new['tables']}")
    return "better", "；".join(reasons) or "API读取成功、无明显倒退"


def _load_fails() -> dict:
    """读取失败计数 {文件名: 限流失败次数}，用于把屡次失败的文档排到队尾。"""
    try:
        return json.load(open(_FAIL_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_fails(d: dict):
    try:
        json.dump(d, open(_FAIL_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


# 人工待复核跳过清单：被判 worse 的文档(多为图片/设计型、API读出正文很少)，
# 自动记入此清单并在后续每日任务中跳过，避免反复重试浪费配额，待人工判断后处理。
_SKIP_FILE = os.path.join(os.path.dirname(__file__), ".reimport_skip.json")


def _load_skip() -> dict:
    try:
        return json.load(open(_SKIP_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _add_skip(fn: str, reason: str):
    d = _load_skip()
    if fn not in d:
        d[fn] = {"reason": reason, "added": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")}
        try:
            json.dump(d, open(_SKIP_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass


# 人工确认放行清单：经人工判断"API少读的是噪声/图里文字已抽取、新版可用"的文档，
# 即使被规则判 worse 也强制用 API 版入库(算新内容)。例:矢量流程图/架构图类文档——
# 图形是SVG/canvas两版都识别不了，但图里文字API已抽取清晰，应采用新版。
_FORCE_FILE = os.path.join(os.path.dirname(__file__), ".reimport_force.json")


def _load_force() -> list:
    try:
        return list(json.load(open(_FORCE_FILE, encoding="utf-8")))
    except Exception:
        return []


def _is_forced(fn: str) -> bool:
    return any(k in fn for k in _load_force())


def _add_force(key: str):
    d = _load_force()
    if key not in d:
        d.append(key)
        json.dump(d, open(_FORCE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _candidates(only: str = "") -> list[str]:
    """有企微链接的 md 文件列表。屡次限流失败的排到队尾，让配额优先用在没试过/能读的文档上。"""
    fails = _load_fails()
    skip = _load_skip()
    out = []
    for fn in os.listdir(KB):
        if not fn.endswith(".md"):
            continue
        if only and only not in fn:
            continue
        if fn in skip:   # 人工待复核，跳过(避免反复重试浪费配额)
            continue
        p = os.path.join(KB, fn)
        head = open(p, encoding="utf-8", errors="replace").read(4000)
        if re.search(r"(doc|sheet|slide)\.weixin\.qq\.com", head):
            out.append(p)
    # 排序：失败次数少的在前（0 次=没试过/能读，优先），同次数按文件名
    out.sort(key=lambda p: (fails.get(os.path.basename(p), 0), os.path.basename(p)))
    return out


async def _process_one(path: str, sem: asyncio.Semaphore, write: bool, backup_dir: str) -> dict:
    fn = os.path.basename(path)
    old_md = open(path, encoding="utf-8", errors="replace").read()
    url = _src_url(old_md)
    rec = {"file": fn, "url": url[:70]}
    if not url:
        rec["verdict"] = "skip"
        rec["reason"] = "无来源链接"
        return rec
    # 仅处理头部来源是企微文档的（info/腾讯其他不在此列）
    if not re.search(r"(doc|sheet|slide)\.weixin\.qq\.com", url):
        rec["verdict"] = "skip"
        rec["reason"] = "来源非企微文档"
        return rec
    async with sem:
        try:
            raw = await wecom.get_doc_content_retry(url=url)
        except Exception as e:
            rec["verdict"] = "skip"
            rec["reason"] = f"API读取失败: {str(e)[:50]}"
            return rec
        await asyncio.sleep(REQ_GAP)  # 请求间隔，避免触发频率限制
    new_body, n_img = wecom.land_base64_images(raw)
    new_body, _ = wecom.normalize_vertical_tables(new_body)  # 竖排碎片表格保守合并
    # 保留原头部(标题/来源/负责人/父文档) + 新正文
    header = _header(old_md)
    new_md = header + "\n\n" + new_body if header else new_body
    # P0: 表格行级摘要（LLM 理解表格含错乱竖排表，提升表格数据检索命中与LLM可读）
    try:
        from table_summary import append_table_summaries_llm
        new_md, _ = await append_table_summaries_llm(new_md)
    except Exception:
        pass

    old_m = _metrics(old_md)
    new_m = _metrics(new_md)
    verdict, reason = _judge(old_m, new_m)
    # 人工确认放行：被判 worse 但在放行清单内(如矢量流程图类，图文字已抽取)，强制采用 API 新版
    forced = False
    if verdict == "worse" and _is_forced(fn) and new_m["text_len"] > 50:
        verdict, reason, forced = "better", f"人工放行(原判:{reason})", True
    rec.update({
        "verdict": verdict, "reason": reason, "landed_imgs": n_img,
        "forced": forced, "old": old_m, "new": new_m,
    })

    if write and verdict == "better":
        # 旧版永久保留到 knowledge_base_old/（供前端"查看旧"），再覆盖为新版
        os.makedirs(OLD_DIR, exist_ok=True)
        shutil.copy2(path, os.path.join(OLD_DIR, fn))
        # 额外一份带时间戳的备份目录（整批可回滚）
        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_dir, fn))
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_md)
        # DB 标记有旧版 + 更新大小 + 刷新上传时间(便于按最新排序验收)
        try:
            import sqlite3
            now = datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE documents SET has_old_version=1, file_size=?, uploaded_at=? WHERE stored_path=?",
                        (os.path.getsize(path), now, fn))
            con.commit(); con.close()
        except Exception:
            pass
        rec["written"] = True
    return rec


async def main():
    args = sys.argv[1:]
    write = "--write" in args
    dry = "--dry-run" in args or not write
    only = ""
    limit = 0
    for i, a in enumerate(args):
        if a == "--only" and i + 1 < len(args):
            only = args[i + 1]
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])

    cands = _candidates(only)
    # --resume：跳过已成功重导的(有旧版备份)，只跑剩下的
    if "--resume" in args:
        import sqlite3
        con = sqlite3.connect(DB_PATH)
        done_set = {r[0] for r in con.execute("SELECT stored_path FROM documents WHERE has_old_version=1").fetchall()}
        con.close()
        before = len(cands)
        cands = [p for p in cands if os.path.basename(p) not in done_set]
        print(f"[resume] 跳过已重导 {before - len(cands)} 篇，剩 {len(cands)} 篇", flush=True)
    if limit:
        cands = cands[:limit]
    ts = datetime.now(_BJ).strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(os.path.dirname(__file__), f"knowledge_base_backup_{ts}")

    mode = "WRITE(写盘)" if write else "DRY-RUN(只读)"
    max_ok = 0
    for i, a in enumerate(args):
        if a == "--max" and i + 1 < len(args):
            max_ok = int(args[i + 1])
    print(f"[{mode}] 候选 {len(cands)} 篇，串行+间隔{REQ_GAP}s" + (f"，本批上限 {max_ok} 篇成功即停" if max_ok else ""), flush=True)

    sem = asyncio.Semaphore(1)
    results = []
    ok = 0
    consec_rl = 0  # 连续限流计数，连续多次说明配额耗尽，提前收手
    fails = _load_fails()
    for idx, p in enumerate(cands):
        r = await _process_one(p, sem, write, backup_dir)
        results.append(r)
        print(f"  ({idx+1}/{len(cands)}) [{r['verdict']}] {r['file'][:34]} — {r.get('reason','')}", flush=True)
        fn = r["file"]
        if r.get("written"):
            ok += 1
        is_rl = "851010" in r.get("reason", "") or "851000" in r.get("reason", "")
        if is_rl:
            # 记录限流失败次数，下批排队尾，避免每批都先撞同一批读不到的文档
            fails[fn] = fails.get(fn, 0) + 1
            consec_rl += 1
            if consec_rl >= 5:
                print("  [早停] 连续 5 次限流，配额已耗尽，本批结束，等下次定时任务继续", flush=True)
                break
        else:
            consec_rl = 0
            if r["verdict"] in ("better", "worse", "same") and fn in fails:
                fails.pop(fn, None)  # 这次读成功了，清掉失败计数
            # 判定 worse(读成功但有明显倒退)：记入人工待复核跳过清单，后续不再反复重试
            if write and r["verdict"] == "worse":
                _add_skip(fn, r.get("reason", "worse"))
        if max_ok and ok >= max_ok:
            print(f"  [达上限] 本批已成功 {ok} 篇，结束", flush=True)
            break
    _save_fails(fails)

    # 汇总
    from collections import Counter
    vc = Counter(r["verdict"] for r in results)
    print("\n=== 汇总 ===", flush=True)
    print("  better(可替换):", vc.get("better", 0), flush=True)
    print("  worse(存疑,需人工):", vc.get("worse", 0), flush=True)
    print("  same(无显著差异):", vc.get("same", 0), flush=True)
    print("  skip(读取失败/无链接):", vc.get("skip", 0), flush=True)
    if write:
        written = sum(1 for r in results if r.get("written"))
        print(f"  已替换写盘: {written} 篇，原文备份在 {backup_dir}", flush=True)
        # 记录本批成功写盘的文件名，供后续"当天新增文档"评测使用
        written_files = [r["file"] for r in results if r.get("written")]
        last_batch = {"ts": ts, "files": written_files, "backup_dir": backup_dir}
        json.dump(last_batch, open(_LAST_BATCH_FILE, "w", encoding="utf-8"), ensure_ascii=False)

    # 报告落盘
    report = os.path.join(os.environ.get("TEMP", "/tmp"), f"wecom_reimport_report_{ts}.json")
    json.dump(results, open(report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"  详细报告: {report}", flush=True)
    # worse 清单单列
    worse = [r for r in results if r["verdict"] == "worse"]
    if worse:
        print("\n=== worse 存疑清单(需人工复核) ===", flush=True)
        for r in worse:
            print(f"  {r['file']}: {r['reason']}", flush=True)


if __name__ == "__main__":
    if "--list-skip" in sys.argv:
        sk = _load_skip()
        print(f"人工待复核跳过清单 共 {len(sk)} 篇：")
        for fn, info in sk.items():
            print(f"  · {fn}\n    原因:{info.get('reason','')} 记于:{info.get('added','')}")
    elif "--clear-skip" in sys.argv:
        # 指定文件名移出清单(人工处理后)，或 --clear-skip all 清空
        target = sys.argv[sys.argv.index("--clear-skip") + 1] if len(sys.argv) > sys.argv.index("--clear-skip") + 1 else ""
        sk = _load_skip()
        if target == "all":
            sk = {}
        else:
            sk = {k: v for k, v in sk.items() if target not in k}
        json.dump(sk, open(_SKIP_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"已更新跳过清单，剩 {len(sk)} 篇")
    elif "--add-force" in sys.argv:
        # 人工确认某文档(关键词)放行：即使判worse也强制用API新版入库；并移出跳过清单
        key = sys.argv[sys.argv.index("--add-force") + 1]
        _add_force(key)
        sk = _load_skip()
        sk = {k: v for k, v in sk.items() if key not in k}
        json.dump(sk, open(_SKIP_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"已加入放行清单: {key}；并移出跳过清单(剩{len(sk)}篇)。下次重导将强制用API版入库")
    elif "--list-force" in sys.argv:
        print("人工放行清单:", _load_force())
    else:
        asyncio.run(main())
