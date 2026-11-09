"""Chat Router - Main conversation and testing interaction endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from database import get_db
from models.project import Project
from models.settings import Settings
from models.skill import Skill
from agents.orchestrator import AgentOrchestrator
from services.llm_router import LLMRouter
from routers.websocket import manager

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessageRequest(BaseModel):
    project_id: int
    message: str
    attachments: Optional[List[Dict[str, Any]]] = None


async def get_orchestrator(db: AsyncSession) -> AgentOrchestrator:
    res = await db.execute(select(Settings))
    all_settings = {s.key: s.value for s in res.scalars().all()}

    provider = all_settings.get("llm_provider", "openai")
    api_key = all_settings.get("llm_api_key", "mock")
    model = all_settings.get("llm_model", "gpt-4")
    default_reporter = all_settings.get("default_reporter", "Mohamed Kamel")

    llm = LLMRouter(provider=provider, api_key=api_key, model=model)
    return AgentOrchestrator(llm_router=llm, jira_settings=all_settings, default_reporter=default_reporter)


@router.post("")
async def send_chat_message(data: ChatMessageRequest, db: AsyncSession = Depends(get_db)):
    """Process incoming chat message from Tester.
    
    Can automatically detect bug reports, requirement uploads, format changes, or QA queries.
    """
    project = await db.get(Project, data.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    text = data.message.strip()
    orchestrator = await get_orchestrator(db)

    # 1. Check if user is defining a report format
    if "report format" in text.lower() or "format of the report" in text.lower():
        skill = Skill(
            project_id=data.project_id,
            type="report_format",
            title="Custom Bug Report Format",
            content=text,
        )
        db.add(skill)
        await db.commit()
        return {
            "type": "skill_saved",
            "agent": "Reporter",
            "response": "✅ I have recorded your custom **Bug Report Format** as a project skill. The Reporter Agent will strictly format all future bug reports according to this template.",
            "data": skill.to_dict(),
        }

    # 2. Check if user is uploading a requirement or user story
    if text.lower().startswith("requirement:") or text.lower().startswith("user story:") or text.lower().startswith("business logic:"):
        skill = Skill(
            project_id=data.project_id,
            type="requirement",
            title=text.split("\n")[0][:60],
            content=text,
        )
        db.add(skill)
        await db.commit()
        return {
            "type": "skill_saved",
            "agent": "Detective",
            "response": f"📋 Requirement stored as a project skill: **{skill.title}**. All agents will now use this context.",
            "data": skill.to_dict(),
        }

    # 3. Otherwise, trigger full Multi-Agent Bug Tracing & Reporting Pipeline!
    async def broadcast_ws(payload: Dict[str, Any]):
        await manager.broadcast(payload)

    pipeline_result = await orchestrator.process_bug_pipeline(
        bug_input=text,
        project_id=data.project_id,
        broadcast_callback=broadcast_ws,
    )

    summary_response = (
        f"### 🎯 Bug Pipeline Completed Successfully!\n\n"
        f"**Title:** {pipeline_result.get('title')}\n"
        f"**Judge Confirmed Root Cause:** {pipeline_result.get('root_cause')}\n"
        f"**Jira Ticket:** [{pipeline_result['jira_ticket']['key']}]({pipeline_result['jira_ticket']['url']}) (Status: {pipeline_result['jira_ticket']['status']})\n"
        f"**Priority:** `{pipeline_result['jira_ticket']['priority']}` | **Label:** `{pipeline_result['jira_ticket']['label']}` | **Reporter:** `{pipeline_result['jira_ticket']['reporter']}`\n"
        f"**Evidence:** {len(pipeline_result.get('evidence', []))} file(s) captured by Journalist.\n\n"
        f"The Watcher Agent is now actively monitoring this ticket for the `In QA` transition."
    )

    return {
        "type": "pipeline_complete",
        "response": summary_response,
        "pipeline_result": pipeline_result,
    }

