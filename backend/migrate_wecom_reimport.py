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
_BJ = timezone(timedelta(hours=8))
REQ_GAP = 8.0  # 每次读取后的间隔(秒)。企微限流较紧，串行+长间隔最稳（慢但避免触顶）

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
    """对比新旧指标，返回 (verdict, reason)。verdict ∈ better/worse/same。"""
    reasons = []
    better_signals = 0
    worse_signals = 0
    # 乱码大幅减少 = 强烈 better
    if old["garble"] > 5 and new["garble"] < old["garble"] * 0.3:
        better_signals += 2
        reasons.append(f"乱码 {old['garble']}→{new['garble']}")
    # 图片大增 = 强烈 better（Playwright 几乎抓不到图，API 能完整拿到）
    if new["imgs"] > old["imgs"]:
        better_signals += 2 if new["imgs"] - old["imgs"] >= 3 else 1
        reasons.append(f"图片 {old['imgs']}→{new['imgs']}")
    # 表格结构化明显增多（乱码表格被修复成真表格）
    if new["tables"] > old["tables"] * 1.5 and new["tables"] - old["tables"] >= 5:
        better_signals += 1
        reasons.append(f"表格 {old['tables']}→{new['tables']}")
    # 正文骤减 = 存疑信号；但若同时有图片大增/乱码减少，说明旧正文是噪声虚胖，不算 worse
    if old["text_len"] > 200 and new["text_len"] < old["text_len"] * 0.4:
        worse_signals += 2
        reasons.append(f"正文 {old['text_len']}→{new['text_len']}")
    # 判定：better 信号能压过 worse（图片/乱码/表格改善优先于"正文变短"）
    if better_signals >= worse_signals and better_signals >= 1:
        return "better", "；".join(reasons)
    if worse_signals >= 2:
        return "worse", "；".join(reasons)
    if better_signals >= 1:
        return "better", "；".join(reasons)
    return "same", "；".join(reasons) or "无显著差异"


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


def _candidates(only: str = "") -> list[str]:
    """有企微链接的 md 文件列表。屡次限流失败的排到队尾，让配额优先用在没试过/能读的文档上。"""
    fails = _load_fails()
    out = []
    for fn in os.listdir(KB):
        if not fn.endswith(".md"):
            continue
        if only and only not in fn:
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
    # 保留原头部(标题/来源/负责人/父文档) + 新正文
    header = _header(old_md)
    new_md = header + "\n\n" + new_body if header else new_body

    old_m = _metrics(old_md)
    new_m = _metrics(new_md)
    verdict, reason = _judge(old_m, new_m)
    rec.update({
        "verdict": verdict, "reason": reason, "landed_imgs": n_img,
        "old": old_m, "new": new_m,
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
    asyncio.run(main())
