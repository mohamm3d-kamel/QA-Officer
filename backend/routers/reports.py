"""Summary Reports Router."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Dict, Any

from database import get_db
from models.project import Project
from models.bug_report import BugReport
from models.skill import Skill

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/dashboard")
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Return aggregated dashboard KPI counts across all projects in two lightweight queries."""
    proj_count_res = await db.execute(select(func.count(Project.id)))
    total_projects = proj_count_res.scalar() or 0

    bugs_res = await db.execute(
        select(BugReport.status, BugReport.jira_ticket_id)
    )
    rows = bugs_res.all()

    return {
        "total_projects": total_projects,
        "total_bugs": len(rows),
        "jira_bugs": sum(1 for _, jira_id in rows if jira_id),
        "resolved_bugs": sum(1 for st, _ in rows if st == "resolved"),
        "failed_bugs": sum(1 for st, _ in rows if st == "failed"),
        "confirmed_bugs": sum(1 for st, _ in rows if st == "confirmed"),
    }


@router.get("/summary/{project_id}")
async def generate_summary_report(project_id: int, db: AsyncSession = Depends(get_db)):
    """Generate comprehensive QA summary report for a project."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Fetch all bugs for project
    res_bugs = await db.execute(
        select(BugReport).where(BugReport.project_id == project_id)
    )
    bugs = res_bugs.scalars().all()

    # Fetch all skills (requirements)
    res_skills = await db.execute(
        select(Skill).where(Skill.project_id == project_id)
    )
    skills = res_skills.scalars().all()

    total_tickets = len(bugs)
    resolved_count = sum(1 for b in bugs if b.status == "resolved")
    failed_count = sum(1 for b in bugs if b.status == "failed")
    in_qa_count = sum(1 for b in bugs if b.status == "in_qa")
    reported_count = sum(1 for b in bugs if b.status == "reported")

    # Group by module/feature (case-insensitive normalization)
    modules_map: Dict[str, Dict[str, Any]] = {}
    canonical_keys: Dict[str, str] = {}

    def _get_or_init_module(raw_name: str) -> str:
        clean = (raw_name or "General").strip() or "General"
        key = clean.lower()
        if key not in canonical_keys:
            canonical_keys[key] = clean
            modules_map[clean] = {"total": 0, "resolved": 0, "failed": 0, "open": 0}
        return canonical_keys[key]

    for b in bugs:
        mod = _get_or_init_module(b.module or "General")
        modules_map[mod]["total"] += 1
        if b.status == "resolved":
            modules_map[mod]["resolved"] += 1
        elif b.status == "failed":
            modules_map[mod]["failed"] += 1
        else:
            modules_map[mod]["open"] += 1

    # Add functional modules from requirement/test_case/business_doc skills (skip report_format templates)
    for s in skills:
        if s.type == "report_format":
            continue
        mod_name = s.title.split("-")[0].strip()[:40]
        if mod_name:
            _get_or_init_module(mod_name)

    # Coverage percentage calculation
    total_modules = len(modules_map)
    covered_modules = sum(1 for _, stats in modules_map.items() if stats["total"] > 0)
    coverage_percentage = round((covered_modules / total_modules) * 100, 1) if total_modules > 0 else 100.0

    return {
        "project_id": project_id,
        "project_name": project.name,
        "coverage_percentage": coverage_percentage,
        "covered_modules": covered_modules,
        "total_modules": total_modules or 1,
        "tickets_added": total_tickets,
        "tickets_resolved": resolved_count,
        "tickets_failed": failed_count,
        "tickets_in_qa": in_qa_count,
        "tickets_reported": reported_count,
        "module_breakdown": [
            {
                "module": mod,
                "total_tickets": stats["total"],
                "resolved": stats["resolved"],
                "failed": stats["failed"],
                "open": stats["open"],
                "status": "Healthy" if stats["failed"] == 0 and stats["open"] == 0 and stats["total"] > 0 else ("Attention" if stats["failed"] > 0 else "In Progress"),
            }
            for mod, stats in modules_map.items()
        ],
        "all_tickets": [b.to_dict() for b in bugs],
    }

