"""Golden Set 文档元数据助手：为文档名解析 标签(tags) 与 活动期(era)。

标签取自 metadata.db 的 document_tags（与生产一致）；活动期从文件名启发式推断
（uploaded_at 是导入时间不代表内容时期，故用主题期）。供出题分层与标签过滤评测复用。
"""
import sqlite3
import os
from functools import lru_cache

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from config import load_settings  # noqa: E402

ERA_RULES = [
    ("S15/赛事", ("S15", "S赛", "LPL", "lol", "英雄联盟", "竞猜", "赛事", "峡谷")),
    ("CNY/春晚", ("CNY", "春晚", "红包", "cny")),
    ("阅兵", ("阅兵",)),
]


def era_of(name: str) -> str:
    for era, kws in ERA_RULES:
        if any(k in name for k in kws):
            return era
    return "其他/通用"


@lru_cache(maxsize=1)
def _doc_tag_map() -> dict:
    """{stored_path basename -> [tag names]}。一次性查库缓存。"""
    db = sqlite3.connect(load_settings().db_path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT d.stored_path, t.name FROM documents d
           JOIN document_tags dt ON d.id = dt.document_id
           JOIN tags t ON t.id = dt.tag_id"""
    ).fetchall()
    db.close()
    m: dict = {}
    for r in rows:
        key = os.path.basename(r["stored_path"])
        m.setdefault(key, []).append(r["name"])
    return m


def tags_of(stored_name: str) -> list:
    """按文件名(basename)返回其标签列表。"""
    return _doc_tag_map().get(os.path.basename(stored_name), [])


def enrich(relevant_files: list) -> dict:
    """给一组 relevant_files 汇总 tags(并集) 与 era(取首个文件)。"""
    tags: list = []
    for f in relevant_files:
        for t in tags_of(f):
            if t not in tags:
                tags.append(t)
    era = era_of(relevant_files[0]) if relevant_files else "其他/通用"
    return {"tags": tags, "era": era}
