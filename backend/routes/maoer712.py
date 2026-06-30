"""临时任务路由：猫耳712直播活动方案对比TODO提醒。
数据来自 temp_tasks/maoer712/todos.json(Playwright抓取的猫耳方案 vs 作战地图对比产出)。
与知识库完全隔离的临时任务。"""
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from auth import require_login

router = APIRouter(tags=["maoer712"])

_TODOS = Path(__file__).resolve().parent.parent / "temp_tasks" / "maoer712" / "todos.json"


@router.get("/maoer712/todos")
async def get_maoer712_todos(user: dict = Depends(require_login)):
    """返回猫耳712方案对比作战地图的风险盲点TODO。"""
    if not _TODOS.exists():
        raise HTTPException(404, "尚未生成对比分析")
    return json.loads(_TODOS.read_text(encoding="utf-8"))
