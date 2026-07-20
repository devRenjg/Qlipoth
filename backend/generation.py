"""后台回答生成引擎（Route 2）。

把 LLM 回答生成从 HTTP 连接生命周期里剥离：
- 生成在独立 asyncio 后台任务里跑，客户端断开(刷新/切会话/关页)不会中止它。
- 每个活跃生成有一个内存 buffer；SSE 连接只是"订阅"buffer 转发增量，同进程内
  重连能立刻拿到已生成部分并继续跟到完成。
- chunk 增量落库(节流)到 chat_history.answer，进程重启也保留已生成部分；
  重启后残留 status='generating' 由 database.init_db 的孤儿清理标为 error。

对外只暴露：
  start_generation(...) -> history_id   起后台任务并插入占位行
  subscribe(history_id) -> async iterator  订阅增量(供 SSE 转发)
  get_task(history_id)                  取活跃任务(状态查询用，可能为 None)
"""
import asyncio
import json
from datetime import datetime, timezone, timedelta

import aiosqlite

from database import DB_PATH
from llm import stream_answer

_BJ_TZ = timezone(timedelta(hours=8))


def _now_bj() -> str:
    return datetime.now(_BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


# 增量落库节流：每累计这么多字符 或 每这么多秒，落一次库，避免每 chunk 一次写放大。
_DB_FLUSH_CHARS = 200
_DB_FLUSH_SECONDS = 1.5


class GenerationTask:
    """一次后台生成的运行时状态 + buffer。订阅者通过 asyncio 事件感知新增量。"""

    def __init__(self, history_id: int):
        self.history_id = history_id
        self.text = ""            # 已生成的全部文本
        self.status = "generating"  # generating / done / error
        self.error = ""           # status=error 时的错误信息
        self.timing: dict | None = None  # 完成时的耗时统计
        self._event = asyncio.Event()    # 有新增量或状态变化时 set
        self._done = False

    def _notify(self):
        self._event.set()

    def append(self, chunk: str):
        self.text += chunk
        self._notify()

    def finish(self, status: str, error: str = "", timing: dict | None = None):
        self.status = status
        self.error = error
        self.timing = timing
        self._done = True
        self._notify()

    async def wait_change(self):
        """等到有新增量/状态变化。返回后调用方应清事件。"""
        await self._event.wait()
        self._event.clear()


# 活跃生成注册表：history_id -> GenerationTask。完成后延迟清理，给刚好在完成
# 瞬间订阅的连接一个拿到终态的窗口。
_tasks: dict[int, GenerationTask] = {}


def get_task(history_id: int) -> GenerationTask | None:
    return _tasks.get(history_id)


async def _insert_placeholder(user_id: int, question: str, source_urls: list,
                              conversation_id: str | None, selected_tags: list) -> int:
    """插入 status='generating' 占位行(answer='' 因 NOT NULL)，返回 history_id。"""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO chat_history "
            "(user_id, question, answer, source_urls, conversation_id, selected_tags, created_at, status, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'generating', ?)",
            (
                user_id,
                question,
                "",
                json.dumps(source_urls, ensure_ascii=False),
                conversation_id,
                json.dumps(selected_tags, ensure_ascii=False) if selected_tags else None,
                _now_bj(),
                _now_bj(),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def _flush_answer(history_id: int, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chat_history SET answer = ?, updated_at = ? WHERE id = ?",
            (text, _now_bj(), history_id),
        )
        await db.commit()


async def _finalize(history_id: int, text: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE chat_history SET answer = ?, status = ?, updated_at = ? WHERE id = ?",
            (text, status, _now_bj(), history_id),
        )
        await db.commit()


async def _run_generation(task: GenerationTask, resolved_q: str, search_text: str,
                          history_block: str, model: str, t_start: float,
                          extra_timing: dict):
    """后台生成主体：流式取 chunk → append 到 buffer + 节流落库 → 完成/出错落终态。

    不绑定任何 HTTP 连接；即使所有订阅者断开也会跑到完成。异常一律吞进 status=error，
    绝不 raise(否则 asyncio 后台任务异常无人 await 会变成 "Task exception never retrieved")。
    """
    import time
    t0 = time.perf_counter()
    last_flush_len = 0
    last_flush_t = t0
    try:
        async for chunk in stream_answer(resolved_q, search_text,
                                         history_block=history_block, model=model):
            task.append(chunk)
            now = time.perf_counter()
            if (len(task.text) - last_flush_len >= _DB_FLUSH_CHARS
                    or now - last_flush_t >= _DB_FLUSH_SECONDS):
                await _flush_answer(task.history_id, task.text)
                last_flush_len = len(task.text)
                last_flush_t = now

        t_answer = time.perf_counter() - t0
        timing = {
            "total": round(time.perf_counter() - t_start, 2),
            "answer": round(t_answer, 2),
            **extra_timing,
        }
        await _finalize(task.history_id, task.text, "done")
        task.finish("done", timing=timing)
    except Exception as e:  # noqa: BLE001 - 后台任务必须兜住所有异常
        # 已生成的部分保留，状态标 error；答案全空则补提示(answer NOT NULL)
        text = task.text or "抱歉，本次回答生成失败，请重试。"
        try:
            await _finalize(task.history_id, text, "error")
        except Exception:
            pass
        task.text = text
        task.finish("error", error=str(e))
    finally:
        # 延迟清理注册表：给完成瞬间才订阅的连接留 30s 拿终态窗口
        async def _cleanup():
            await asyncio.sleep(30)
            _tasks.pop(task.history_id, None)
        asyncio.create_task(_cleanup())


async def start_generation(*, user_id: int, question: str, resolved_q: str,
                           search_text: str, history_block: str, model: str,
                           source_urls: list, conversation_id: str | None,
                           selected_tags: list, t_start: float,
                           extra_timing: dict) -> int:
    """插入占位行 + 起后台生成任务，立即返回 history_id(不等生成完成)。"""
    history_id = await _insert_placeholder(
        user_id, question, source_urls, conversation_id, selected_tags,
    )
    task = GenerationTask(history_id)
    _tasks[history_id] = task
    asyncio.create_task(
        _run_generation(task, resolved_q, search_text, history_block, model,
                        t_start, extra_timing)
    )
    return history_id


async def subscribe(history_id: int):
    """订阅某活跃生成，产出 ('chunk', text) 增量与最终 ('done', timing)/('error', msg)。

    从 buffer 已有内容开始补发(重连续显)，随后跟随增量直到终态。若 history_id 不在
    活跃注册表(进程重启后/已清理)，产出 ('gone', None) 让调用方回退查库。
    """
    task = _tasks.get(history_id)
    if task is None:
        yield ("gone", None)
        return

    sent = 0
    # 先补发 buffer 里已有的内容(重连时立刻续上)
    if task.text:
        yield ("chunk", task.text[sent:])
        sent = len(task.text)

    while True:
        if len(task.text) > sent:
            yield ("chunk", task.text[sent:])
            sent = len(task.text)
        if task._done:
            if task.status == "error":
                yield ("error", task.error)
            else:
                yield ("done", task.timing or {})
            return
        await task.wait_change()
