import re
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
import aiosqlite
from database import DB_PATH
from auth import COOKIE_NAME

router = APIRouter(tags=["openspec"])

# backend/routes/openspec.py -> repo_root/openspec
_OPENSPEC_DIR = Path(__file__).resolve().parents[2] / "openspec"
_SPECS_DIR = _OPENSPEC_DIR / "specs"
_CHANGES_DIR = _OPENSPEC_DIR / "changes"


async def _require_admin(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "未登录")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT role FROM users WHERE token = ?", (token,))
        caller = await cursor.fetchone()
        if not caller or caller["role"] != "admin":
            raise HTTPException(403, "无权限")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extract_purpose(text: str) -> str:
    """Grab the body under '## Purpose' up to the next heading."""
    m = re.search(r"^##\s+Purpose\s*\n(.*?)(?=^#{1,2}\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def _parse_requirements(text: str) -> list[dict]:
    """Parse '### Requirement: <name>' blocks, each with '#### Scenario:' items."""
    requirements = []
    req_blocks = re.split(r"^###\s+Requirement:\s*", text, flags=re.M)[1:]
    for block in req_blocks:
        name, _, rest = block.partition("\n")
        scenarios = []
        scen_blocks = re.split(r"^####\s+Scenario:\s*", rest, flags=re.M)
        intro = scen_blocks[0].strip()
        for scen in scen_blocks[1:]:
            sname, _, sbody = scen.partition("\n")
            scenarios.append({"name": sname.strip(), "body": sbody.strip()})
        requirements.append({
            "name": name.strip(),
            "description": intro,
            "scenarios": scenarios,
        })
    return requirements


def _parse_tasks(text: str) -> dict:
    done = len(re.findall(r"^\s*-\s*\[x\]", text, re.M | re.I))
    total = len(re.findall(r"^\s*-\s*\[[ xX]\]", text, re.M))
    return {"done": done, "total": total}


@router.get("/openspec/specs")
async def list_specs(request: Request):
    """List implemented capabilities from openspec/specs/."""
    await _require_admin(request)
    result = []
    if _SPECS_DIR.is_dir():
        for cap_dir in sorted(_SPECS_DIR.iterdir()):
            spec_file = cap_dir / "spec.md"
            if not (cap_dir.is_dir() and spec_file.is_file()):
                continue
            text = _read(spec_file)
            requirements = _parse_requirements(text)
            result.append({
                "name": cap_dir.name,
                "purpose": _extract_purpose(text),
                "requirement_count": len(requirements),
                "requirements": requirements,
            })
    return result


@router.get("/openspec/changes")
async def list_changes(request: Request):
    """List pending proposals from openspec/changes/ (excludes archive)."""
    await _require_admin(request)
    result = []
    if _CHANGES_DIR.is_dir():
        for change_dir in sorted(_CHANGES_DIR.iterdir()):
            if not change_dir.is_dir() or change_dir.name == "archive":
                continue
            proposal_file = change_dir / "proposal.md"
            if not proposal_file.is_file():
                continue
            proposal_text = _read(proposal_file)
            tasks = _parse_tasks(_read(change_dir / "tasks.md"))
            result.append({
                "name": change_dir.name,
                "why": _extract_section(proposal_text, "Why"),
                "what_changes": _extract_section(proposal_text, "What Changes"),
                "tasks": tasks,
            })
    return result


def _extract_section(text: str, heading: str) -> str:
    m = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s|\Z)",
        text, re.M | re.S,
    )
    return m.group(1).strip() if m else ""
