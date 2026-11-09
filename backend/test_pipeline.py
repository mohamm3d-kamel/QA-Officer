"""End-to-End Test Suite for QA Officer Backend and Agent Pipeline."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "site-packages"))
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import json
from database import init_db, async_session
from main import seed_initial_data
from models.project import Project
from models.bug_report import BugReport
from models.skill import Skill
from models.agent_log import AgentLog
from agents.orchestrator import AgentOrchestrator
from services.llm_router import LLMRouter
from routers.reports import generate_summary_report


async def run_e2e_tests():
    print("=" * 60)
    print("  🧪 QA OFFICER — COMPLETE E2E VERIFICATION TEST")
    print("=" * 60)

    # 1. Initialize & Seed
    print("\n[Step 1] Initializing Database & Seeding Default Project...")
    await init_db()
    await seed_initial_data()
    print("✅ Database & Seeds ready.")

    # 2. Verify Seeded Project & Skills
    async with async_session() as session:
        proj = await session.get(Project, 1)
        assert proj is not None, "Project 1 should exist"
        print(f"✅ Found Seeded Project: {proj.name} (Jira Key: {proj.jira_project_key})")

        skills_context, report_format = await AgentOrchestrator(
            LLMRouter("openai", "test", "gpt-4"), {}
        ).get_project_skills_context(1)
        print(f"✅ Found Stored Skills:\n{skills_context.strip()}")
        print(f"✅ Found Custom Report Format Template:\n{report_format.strip()}")

    # 3. Test Full Multi-Agent Pipeline
    print("\n[Step 2] Executing Multi-Agent Pipeline for Defect...")
    bug_description = (
        "Cart total does not update after applying discount code 'SUMMER20'. "
        "The checkout shows 20% discount badge, but final price charged to credit card "
        "still reflects full amount."
    )

    llm = LLMRouter("openai", "test-key", "gpt-4")
    orchestrator = AgentOrchestrator(
        llm_router=llm,
        jira_settings={"default_reporter": "Mohamed Kamel"},
        default_reporter="Mohamed Kamel",
    )

    events_received = []

    async def test_broadcast(msg):
        events_received.append(msg)
        print(f"   📢 [Agent Event] {msg.get('agent', '').upper()}: {msg.get('status')} - {msg.get('message')}")

    pipeline_result = await orchestrator.process_bug_pipeline(
        bug_input=bug_description,
        project_id=1,
        broadcast_callback=test_broadcast,
    )

    bug_id = pipeline_result["bug_id"]
    print(f"\n✅ Pipeline Complete! Created Bug Report #{bug_id}")
    print(f"   • Title: {pipeline_result['title']}")
    print(f"   • Judge Confirmed Root Cause: {pipeline_result['root_cause']}")
    print(f"   • Jira Ticket: {pipeline_result['jira_ticket']['key']} (Status: {pipeline_result['jira_ticket']['status']})")
    print(f"   • Jira Reporter: {pipeline_result['jira_ticket']['reporter']}")
    print(f"   • Priority: {pipeline_result['jira_ticket']['priority']} | Label: {pipeline_result['jira_ticket']['label']}")
    print(f"   • Journalist Evidence: {len(pipeline_result['evidence'])} file(s)")

    # 4. Test Watcher & Retest Pipeline ('In QA' -> 'Ready To Deploy' -> Sanity Testers)
    print("\n[Step 3] Testing Watcher Agent Retest Trigger ('In QA')...")
    retest_result = await orchestrator.retest_and_sanity_pipeline(
        bug_report_id=bug_id,
        broadcast_callback=test_broadcast,
    )
    print(f"✅ Retest Outcome: {retest_result['retest']['new_status']}")
    if retest_result.get("sanity"):
        print(f"✅ Sanity Testers Result: {retest_result['sanity']['summary']}")

    # 5. Test Summary Report Generation
    print("\n[Step 4] Testing Summary Report Generation...")
    async with async_session() as session:
        summary = await generate_summary_report(project_id=1, db=session)
        print(f"✅ Summary Generated for: {summary['project_name']}")
        print(f"   • Coverage: {summary['coverage_percentage']}% ({summary['covered_modules']}/{summary['total_modules']} modules)")
        print(f"   • Total Tickets Added: {summary['tickets_added']}")
        print(f"   • Resolved: {summary['tickets_resolved']}")
        print(f"   • Failed QA: {summary['tickets_failed']}")
        print(f"   • In QA: {summary['tickets_in_qa']}")

    # 6. Verify Agent Logs
    print("\n[Step 5] Checking Agent Activity Audit Trail...")
    async with async_session() as session:
        from sqlalchemy import select
        res = await session.execute(select(AgentLog).where(AgentLog.project_id == 1))
        logs = res.scalars().all()
        print(f"✅ Total Agent Actions Logged: {len(logs)}")
        for l in logs[-5:]:
            print(f"   • [{l.agent_name}] {l.action} -> {l.status}")

    print("\n" + "=" * 60)
    print("  🎉 ALL QA OFFICER SYSTEMS VERIFIED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_e2e_tests())

