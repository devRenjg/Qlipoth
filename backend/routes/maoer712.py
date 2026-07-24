"""临时任务路由：猫耳712直播活动方案对比 + 复盘分析。
数据来源：
- 事前盲点提醒：temp_tasks/maoer712/todos.json(Playwright抓取的猫耳方案 vs 作战地图对比产出)
- 事后复盘分析：temp_tasks/maoer712_review/deliver/*.md(复盘文档定稿报告 + 安全维度独立复核)
与知识库完全隔离的临时任务。"""
import json
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from auth import require_login

router = APIRouter(tags=["maoer712"])

_BASE = Path(__file__).resolve().parent.parent / "temp_tasks"
_TODOS = _BASE / "maoer712" / "todos.json"
_REVIEW_DIR = _BASE / "maoer712_review" / "deliver"
_REVIEW_REPORT = _REVIEW_DIR / "猫耳712复盘分析报告.md"
_REVIEW_SECURITY = _REVIEW_DIR / "猫耳712安全维度独立复核结论.md"


@router.get("/maoer712/todos")
async def get_maoer712_todos(user: dict = Depends(require_login)):
    """返回猫耳712方案对比作战地图的风险盲点TODO（事前盲点提醒）。"""
    if not _TODOS.exists():
        raise HTTPException(404, "尚未生成对比分析")
    return json.loads(_TODOS.read_text(encoding="utf-8"))


@router.get("/maoer712/review")
async def get_maoer712_review(user: dict = Depends(require_login)):
    """返回猫耳712复盘分析报告定稿正文 + 安全维度独立复核结论(均为 Markdown)。"""
    if not _REVIEW_REPORT.exists():
        raise HTTPException(404, "尚未生成复盘分析报告")
    return {
        "report": _REVIEW_REPORT.read_text(encoding="utf-8"),
        "security": _REVIEW_SECURITY.read_text(encoding="utf-8")
        if _REVIEW_SECURITY.exists()
        else "",
    }
