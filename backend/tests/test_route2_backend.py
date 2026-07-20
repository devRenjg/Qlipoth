"""Route 2 后端集成测试（不依赖 pytest，直接 py -3.12 运行）。

覆盖：
  1. 占位行插入 + 增量落库 + done 落终态
  2. /chat/turn 状态查询（内存活跃任务 + 落库回退）
  3. 客户端断开后台任务仍跑完并落库（"切走/刷新照样完整"的根本保障）
  4. 生成出错标 status=error、保留已生成部分
  5. get_conversation 返回 status 字段
  6. 匿名访客(id=0)……本项目 require_login 访客 id=0，Route 2 仍插行(问答归属)，
     但登录用户/访客区分由 user['id'] 决定——此处验证登录用户正常落库
  7. 孤儿清理：init_db 把残留 generating 标 error

运行：py -3.12 tests/test_route2_backend.py
"""
import asyncio
import os
import sys
import tempfile
import time

# 让 backend 根目录可导入（tests 在其下）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

# ---- 用临时 DB，避免污染真实 metadata.db ----
_TMP_DB = os.path.join(tempfile.gettempdir(), f"route2_test_{os.getpid()}.db")

import database
database.DB_PATH = _TMP_DB
import generation
generation.DB_PATH = _TMP_DB
import routes.query as query
query.DB_PATH = _TMP_DB

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if detail and not cond:
        line += f"  -> {detail}"
    print(line)


# ---- 可控的假 stream_answer：逐块吐字，慢速以便测"断开后仍跑完" ----
async def fake_stream_ok(question, search_results, history_block="", model=""):
    for part in ["你好", "，这是", "分块", "回答。"]:
        await asyncio.sleep(0.05)
        yield part


async def fake_stream_slow(question, search_results, history_block="", model=""):
    for i in range(6):
        await asyncio.sleep(0.2)
        yield f"块{i}"


async def fake_stream_boom(question, search_results, history_block="", model=""):
    yield "已经生成一部分"
    await asyncio.sleep(0.05)
    raise RuntimeError("LLM 502 模拟错误")


async def query_row(history_id):
    async with aiosqlite.connect(_TMP_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM chat_history WHERE id=?", (history_id,))
        return await cur.fetchone()


async def test_placeholder_and_incremental():
    """占位行立即可见(status=generating)，跑完后 answer 完整、status=done。"""
    generation.stream_answer = fake_stream_ok
    hid = await generation.start_generation(
        user_id=1, question="Q1", resolved_q="Q1", search_text="ctx",
        history_block="", model="m", source_urls=[{"url": "u"}],
        conversation_id="conv-A", selected_tags=[{"id": 1, "name": "T"}],
        t_start=time.perf_counter(), extra_timing={"search": 0.1},
    )
    # 起始瞬间：库里应已有占位行，status=generating
    row = await query_row(hid)
    check("占位行立即插入", row is not None and row["status"] == "generating",
          f"row={dict(row) if row else None}")
    check("占位行 answer 为空串(NOT NULL 兼容)", row is not None and row["answer"] == "")
    check("占位行带 conversation_id", row is not None and row["conversation_id"] == "conv-A")

    # 等生成跑完
    task = generation.get_task(hid)
    for _ in range(100):
        if task._done:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    row = await query_row(hid)
    check("完成后 status=done", row["status"] == "done", f"status={row['status']}")
    check("完成后 answer 完整落库", row["answer"] == "你好，这是分块回答。",
          f"answer={row['answer']!r}")
    check("selected_tags 快照落库", "T" in (row["selected_tags"] or ""),
          f"tags={row['selected_tags']!r}")


async def test_disconnect_still_completes():
    """核心保障：订阅者中途放弃(模拟切走/刷新)，后台任务仍跑完并落库完整答案。"""
    generation.stream_answer = fake_stream_slow
    hid = await generation.start_generation(
        user_id=1, question="Q2", resolved_q="Q2", search_text="ctx",
        history_block="", model="m", source_urls=[],
        conversation_id="conv-B", selected_tags=[],
        t_start=time.perf_counter(), extra_timing={},
    )
    # 只订阅拿前 2 块就"断开"(break 出订阅循环，等价于连接关闭)
    got = 0
    async for kind, payload in generation.subscribe(hid):
        if kind == "chunk":
            got += 1
        if got >= 2:
            break  # 模拟客户端断开
    check("断开前已收到增量", got >= 2, f"got={got}")

    # 后台任务不受影响，继续跑到完成
    task = generation.get_task(hid)
    for _ in range(100):
        if task._done:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    row = await query_row(hid)
    expected = "".join(f"块{i}" for i in range(6))
    check("断开后仍落库完整答案", row["answer"] == expected, f"answer={row['answer']!r}")
    check("断开后 status=done", row["status"] == "done", f"status={row['status']}")


async def test_error_path():
    """生成中途抛错：status=error，已生成部分保留(不丢失)。"""
    generation.stream_answer = fake_stream_boom
    hid = await generation.start_generation(
        user_id=1, question="Q3", resolved_q="Q3", search_text="ctx",
        history_block="", model="m", source_urls=[],
        conversation_id="conv-C", selected_tags=[],
        t_start=time.perf_counter(), extra_timing={},
    )
    task = generation.get_task(hid)
    for _ in range(100):
        if task._done:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.05)
    row = await query_row(hid)
    check("出错 status=error", row["status"] == "error", f"status={row['status']}")
    check("出错保留已生成部分", "已经生成一部分" in row["answer"], f"answer={row['answer']!r}")


async def test_subscribe_reconnect_full():
    """重连订阅：从头补发已生成内容(续显不缺字)，再跟到完成。"""
    generation.stream_answer = fake_stream_slow
    hid = await generation.start_generation(
        user_id=1, question="Q4", resolved_q="Q4", search_text="ctx",
        history_block="", model="m", source_urls=[],
        conversation_id="conv-D", selected_tags=[],
        t_start=time.perf_counter(), extra_timing={},
    )
    # 等它生成一部分
    await asyncio.sleep(0.5)
    # 现在"重连"：新订阅应先补发已有内容，再续到完成
    acc = ""
    done = False
    async for kind, payload in generation.subscribe(hid):
        if kind == "chunk":
            acc += payload
        elif kind == "done":
            done = True
            break
        elif kind in ("error", "gone"):
            break
    expected = "".join(f"块{i}" for i in range(6))
    check("重连补发+续显=完整", acc == expected, f"acc={acc!r}")
    check("重连收到 done", done)


async def test_orphan_cleanup():
    """孤儿清理：手动塞一条 generating 行 → init_db 应标为 error。"""
    async with aiosqlite.connect(_TMP_DB) as db:
        await db.execute(
            "INSERT INTO chat_history (user_id, question, answer, conversation_id, created_at, status, updated_at) "
            "VALUES (1,'orphanQ','','conv-orphan','2026-07-14 00:00:00','generating','2026-07-14 00:00:00')"
        )
        await db.commit()
        cur = await db.execute("SELECT id FROM chat_history WHERE question='orphanQ'")
        orphan_id = (await cur.fetchone())[0]
    await database.init_db()  # 重启模拟：init 时清理孤儿
    row = await query_row(orphan_id)
    check("孤儿 generating 被标 error", row["status"] == "error", f"status={row['status']}")
    check("孤儿空答案补中断提示", "中断" in row["answer"], f"answer={row['answer']!r}")


def _parse_sse(text):
    """从 SSE 响应体解析出事件列表 [(type, data), ...]。"""
    import json
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            obj = json.loads(line[6:])
            events.append((obj.get("type"), obj.get("data")))
    return events


def test_http_stream_and_turn():
    """HTTP 层：/query/stream 返回 meta(含 history_id)+chunk+done；
    /chat/turn 查得到；/chat/conversation 带 status。用 TestClient(同步)。"""
    from fastapi.testclient import TestClient
    from auth import require_login
    import main

    # 覆盖登录依赖 → 固定测试用户；覆盖检索/LLM → 不打真实网关
    main.app.dependency_overrides[require_login] = lambda: {"id": 1, "username": "tester", "role": "admin"}
    generation.stream_answer = fake_stream_ok

    # 避免真实 grep / 策略生成 / 设置校验
    query.load_settings = lambda: type("S", (), {
        "llm_api_key": "k", "llm_model": "m", "llm_model_fast": "mf"})()
    query.generate_search_strategy = _fake_strategy
    query.grep_search = lambda kw, fp: []
    query._assemble_context = lambda results, q: ("ctx", [], [])
    query.route_model = lambda q, m, mf: (m, "general")

    client = TestClient(main.app)
    with client.stream("POST", "/api/query/stream",
                       json={"question": "HTTPQ", "conversation_id": "conv-http", "tag_ids": []}) as resp:
        body = "".join(resp.iter_text())
    events = _parse_sse(body)
    types = [t for t, _ in events]
    meta = next((d for t, d in events if t == "meta"), None)
    check("SSE 首个是 meta", types and types[0] == "meta", f"types={types}")
    check("meta 带 history_id", meta and "history_id" in meta, f"meta_keys={list(meta) if meta else None}")
    chunks = "".join(d for t, d in events if t == "chunk")
    check("SSE chunk 拼接完整", chunks == "你好，这是分块回答。", f"chunks={chunks!r}")
    check("SSE 有 done", "done" in types, f"types={types}")

    hid = meta["history_id"]
    # /chat/turn 查该轮：done 后内存任务可能已清理→回退查库，answer 完整
    r = client.get(f"/api/chat/turn/{hid}")
    check("/chat/turn 200", r.status_code == 200, f"code={r.status_code}")
    j = r.json()
    check("/chat/turn 返回完整答案", j["answer"] == "你好，这是分块回答。", f"j={j}")
    check("/chat/turn status=done", j["status"] == "done", f"status={j['status']}")

    # /chat/conversation 带 status 字段
    r2 = client.get("/api/chat/conversation/conv-http")
    check("/chat/conversation 200", r2.status_code == 200)
    conv = r2.json()
    check("会话轮次带 status", conv and conv[0].get("status") == "done", f"conv={conv}")

    main.app.dependency_overrides.clear()


async def _fake_strategy(q):
    return {"keywords": [q], "file_pattern": "*"}, 0.01


async def main_run():
    await database.init_db()
    await test_placeholder_and_incremental()
    await test_disconnect_still_completes()
    await test_error_path()
    await test_subscribe_reconnect_full()
    await test_orphan_cleanup()


if __name__ == "__main__":
    asyncio.run(main_run())
    # HTTP 测试用 TestClient(自带事件循环)，放异步之外跑
    test_http_stream_and_turn()

    print("\n" + "=" * 50)
    print(f"结果：{len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    else:
        print("全部通过 [OK]")
    # 清理临时库
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass
