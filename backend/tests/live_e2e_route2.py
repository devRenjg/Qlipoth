"""Route 2 活服务器端到端验证：对真实 8000 端口发起流式问答，
读到 history_id + 2 个 chunk 后主动断开(模拟用户切会话/刷新)，
随后轮询 /chat/turn 直到完成，断言后台任务在断开后仍跑完并落库。

用法：py -3.12 tests/live_e2e_route2.py
前置：后端已在 http://127.0.0.1:8000 运行，且已配置 LLM API Key。
"""
import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
QUESTION = "用一句话介绍你自己"  # 通用问题，不依赖具体知识库内容
CONV = "e2e-route2-live"


async def main():
    history_id = None
    chunks_seen = 0
    disconnected = False

    async with httpx.AsyncClient(timeout=120) as client:
        # 1) 发起流式问答，读到 history_id + 2 chunk 后主动断开
        try:
            async with client.stream(
                "POST", f"{BASE}/api/query/stream",
                json={"question": QUESTION, "conversation_id": CONV, "tag_ids": []},
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    print(f"[FAIL] /query/stream 状态码 {resp.status_code}: {body[:200]!r}")
                    return 1
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    obj = json.loads(line[6:])
                    t = obj.get("type")
                    if t == "meta":
                        history_id = obj["data"].get("history_id")
                        print(f"[meta] history_id={history_id}")
                    elif t == "chunk":
                        chunks_seen += 1
                        if chunks_seen <= 2:
                            print(f"[chunk {chunks_seen}] {obj['data'][:20]!r}")
                        if chunks_seen >= 2:
                            disconnected = True
                            break  # 主动断开 SSE = 模拟切会话/刷新
                    elif t == "done":
                        print("[done] 生成在断开前就完成了(答案很短)")
                        break
                    elif t == "error":
                        print(f"[FAIL] 流内错误: {obj['data']}")
                        return 1
        except Exception as e:
            print(f"[warn] 流读取中断(预期，因为我们主动 break): {type(e).__name__}")

        if history_id is None:
            print("[FAIL] 未从 meta 拿到 history_id")
            return 1
        print(f"[info] 已断开(chunks_seen={chunks_seen}, disconnected={disconnected})，"
              f"后台任务应继续。开始轮询 /chat/turn/{history_id} ...")

        # 2) 轮询状态直到 done/error（证明后台任务在断开后仍在跑并落库）
        final = None
        for i in range(150):  # 最多 ~120s
            r = await client.get(f"{BASE}/api/chat/turn/{history_id}")
            if r.status_code != 200:
                print(f"[FAIL] /chat/turn 状态码 {r.status_code}")
                return 1
            data = r.json()
            st = data.get("status")
            if st and st != "generating":
                final = data
                break
            await asyncio.sleep(0.8)

        if final is None:
            print("[FAIL] 轮询超时，任务仍 generating")
            return 1

        ans = final.get("answer") or ""
        print(f"[final] status={final['status']} answer_len={len(ans)}")
        print(f"[final] answer预览: {ans[:80]!r}")

        # 3) 断言
        ok = True
        if final["status"] != "done":
            print(f"[FAIL] 期望 status=done，实得 {final['status']}")
            ok = False
        if len(ans) < 2:
            print("[FAIL] 断开后最终答案为空——后台任务未跑完/未落库")
            ok = False

        # 4) 会话列表该轮 status=done
        r2 = await client.get(f"{BASE}/api/chat/conversation/{CONV}")
        if r2.status_code == 200:
            conv = r2.json()
            last = conv[-1] if conv else None
            if last:
                print(f"[conv] 末轮 status={last.get('status')} answer_len={len(last.get('answer') or '')}")
                if last.get("status") != "done":
                    print(f"[FAIL] 会话末轮 status 非 done: {last.get('status')}")
                    ok = False
        else:
            print(f"[warn] /chat/conversation 状态码 {r2.status_code}(访客隔离可能返回空，非致命)")

        if ok:
            print("\n[PASS] 断开后后台生成仍跑完并落库，重连查得完整答案 [OK]")
            return 0
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
