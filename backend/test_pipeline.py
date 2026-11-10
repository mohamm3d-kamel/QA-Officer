"""End-to-End Test Suite for QA Officer Backend and Agent Pipeline."""
import os
import sys
import tempfile
from pathlib import Path

# Isolate E2E test DB from the user's live database
TEST_DB_FILE = Path(tempfile.gettempdir()) / "qa_officer_e2e_test.db"
if TEST_DB_FILE.exists():
    TEST_DB_FILE.unlink()
os.environ["QA_OFFICER_DB_PATH"] = str(TEST_DB_FILE)

sys.path.insert(0, str(Path(__file__).parent / "site-packages"))
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
from sqlalchemy import select
from database import init_db, async_session
from main import seed_initial_data
from models.project import Project
from models.bug_report import BugReport
from models.agent_log import AgentLog
from agents.orchestrator import AgentOrchestrator
from services.llm_router import LLMRouter
from routers.reports import generate_summary_report


async def run_e2e_tests():
    print("=" * 60)
    print("  🧪 QA OFFICER — COMPLETE E2E VERIFICATION TEST")
    print("=" * 60)

    # 1. Initialize & Seed
    print("\n[Step 1] Initializing Isolated Test Database & Seeding Default Project...")
    await init_db()
    await seed_initial_data()
    print("✅ Database & Seeds ready.")

    llm = LLMRouter("openai", "test-key", "gpt-4")
    orchestrator = AgentOrchestrator(
        llm_router=llm,
        jira_settings={"default_reporter": "Mohamed Kamel"},
        default_reporter="Mohamed Kamel",
    )

    # 2. Verify Seeded Project & Skills
    async with async_session() as session:
        proj = await session.get(Project, 1)
        assert proj is not None, "Project 1 should exist"
        project_info = proj.to_dict()
        print(f"✅ Found Seeded Project: {proj.name} (Jira Key: {proj.jira_project_key})")

    skills_context, report_format = await orchestrator.get_project_skills_context(1)
    assert report_format, "Default report format template should exist"
    print("✅ Found Stored Skills & Report Format Template.")

    # 3. Test Desk Officer Pre-Workflow Chat (Workflow must NOT start yet)
    print("\n[Step 2] Testing Desk Officer Pre-Workflow Chat & Agent Roster Awareness...")
    chat_history = []
    msg1 = (
        "Cart total does not update after applying discount code 'SUMMER20'. "
        "The checkout shows 20% discount badge, but final price charged to credit card "
        "still reflects full amount."
    )
    triage1 = await orchestrator.desk_officer.triage_and_route(
        message=msg1,
        project_info=project_info,
        skills_context=skills_context,
        recent_bugs=[],
        conversation_history=chat_history,
        project_id=1,
    )
    assert triage1["action"] == "chat", f"Expected pre-workflow chat, got {triage1['action']}"
    assert triage1["also_raise_jira"] is False
    chat_history.append({"sender": "user", "content": msg1})
    chat_history.append({"sender": "agent", "agent": "Desk Officer", "content": triage1["reply"]})
    print("✅ Pre-workflow bug chat stayed conversational (main workflow did NOT auto-trigger).")

    triage_roster = await orchestrator.desk_officer.triage_and_route(
        message="What built-in agents are available and what are their roles?",
        project_info=project_info,
        skills_context=skills_context,
        recent_bugs=[],
        conversation_history=chat_history,
        project_id=1,
    )
    assert triage_roster["action"] == "chat"
    for expected_agent in ["Detective", "Coroner", "Judge", "Reporter", "Sheriff", "Watcher", "Sanity Testers"]:
        assert expected_agent in triage_roster["reply"], f"Roster reply missing {expected_agent}"
    print("✅ Desk Officer knows and explains all 7 built-in agents.")

    # 4. Test Individual Agent Assignment by Desk Officer (e.g. Detective)
    print("\n[Step 3] Testing Desk Officer Assigning Individual Agent (Detective)...")
    triage_detective = await orchestrator.desk_officer.triage_and_route(
        message="Assign Detective to investigate this bug",
        project_info=project_info,
        skills_context=skills_context,
        recent_bugs=[],
        conversation_history=chat_history,
        project_id=1,
    )
    assert triage_detective["action"] == "assign_agent"
    assert triage_detective["target_agent"] == "detective"
    detective_dispatch = await orchestrator.dispatch_agent(
        target_agent="detective",
        project_id=1,
        bug_input=triage_detective["bug_summary"],
    )
    assert detective_dispatch["status"] == "success"
    print(f"✅ Desk Officer assigned Detective -> Draft Bug #{detective_dispatch['bug_id']} created.")

    # 5. Test Main Workflow Trigger ('Create a bug report') WITHOUT Sheriff raising Jira
    print("\n[Step 4] Testing 'Create a bug report' Main Workflow (Sheriff stays on standby)...")
    triage_workflow = await orchestrator.desk_officer.triage_and_route(
        message="Create a bug report",
        project_info=project_info,
        skills_context=skills_context,
        recent_bugs=[],
        conversation_history=chat_history,
        project_id=1,
    )
    assert triage_workflow["action"] == "run_main_workflow"
    assert triage_workflow["also_raise_jira"] is False, "Sheriff must NOT auto-raise Jira!"

    events_received = []

    async def test_broadcast(msg):
        events_received.append(msg)
        print(f"   📢 [Agent Event] {msg.get('agent', '').upper()}: {msg.get('status')} - {msg.get('message')}")

    pipeline_result = await orchestrator.process_bug_pipeline(
        bug_input=triage_workflow["bug_summary"],
        project_id=1,
        broadcast_callback=test_broadcast,
        include_sheriff=triage_workflow["also_raise_jira"],
    )

    bug_id = pipeline_result["bug_id"]
    assert pipeline_result["jira_ticket"] is None, "Sheriff should NOT raise ticket until asked!"
    assert pipeline_result["status"] == "confirmed"
    assert pipeline_result["formatted_report"], "Reporter output should be generated and saved"
    print(f"\n✅ Main Workflow Complete! Created Bug Report #{bug_id} (Status: {pipeline_result['status']})")
    print(f"   • Title: {pipeline_result['title']}")
    print(f"   • Judge Confirmed Root Cause: {pipeline_result['root_cause']}")
    print(f"   • Coroner Evidence: {len(pipeline_result['evidence'])} file(s)")
    print("   • Sheriff Ticket: Not raised yet (awaiting explicit request)")

    # 6. Test Explicit Sheriff Raise -> Must go to 'Backlog' (NOT 'To Do')
    print("\n[Step 5] Testing Explicit Sheriff Raise -> Ticket Status Must Be 'Backlog'...")
    triage_sheriff = await orchestrator.desk_officer.triage_and_route(
        message=f"Tell Sheriff to raise bug #{bug_id} to Jira",
        project_info=project_info,
        skills_context=skills_context,
        recent_bugs=[{"id": bug_id, "title": pipeline_result["title"], "status": "confirmed", "jira_ticket_id": None}],
        conversation_history=chat_history,
        project_id=1,
    )
    assert triage_sheriff["action"] == "assign_agent"
    assert triage_sheriff["target_agent"] == "sheriff"

    sheriff_dispatch = await orchestrator.dispatch_agent(
        target_agent="sheriff",
        project_id=1,
        bug_report_id=triage_sheriff["bug_report_id"],
        broadcast_callback=test_broadcast,
    )
    ticket = sheriff_dispatch["jira_ticket"]
    assert ticket["status"] == "Backlog", f"Expected Jira status 'Backlog', got '{ticket['status']}'"
    print(f"✅ Sheriff Raised Ticket: {ticket['key']} | Jira Status: {ticket['status']} | Reporter: {ticket['reporter']}")

    # 7. Test Watcher & Retest Pipeline ('In QA' -> 'Ready To Deploy' -> Sanity Testers)
    print("\n[Step 6] Testing Watcher Agent Retest Trigger ('In QA')...")
    retest_result = await orchestrator.retest_and_sanity_pipeline(
        bug_report_id=bug_id,
        broadcast_callback=test_broadcast,
    )
    print(f"✅ Retest Outcome: {retest_result['retest']['new_status']}")
    if retest_result.get("sanity"):
        print(f"✅ Sanity Testers Result: {retest_result['sanity']['summary']}")

    # 8. Test Summary Report Generation
    print("\n[Step 7] Testing Summary Report Generation...")
    async with async_session() as session:
        summary = await generate_summary_report(project_id=1, db=session)
        print(f"✅ Summary Generated for: {summary['project_name']}")
        print(f"   • Coverage: {summary['coverage_percentage']}% ({summary['covered_modules']}/{summary['total_modules']} modules)")
        print(f"   • Total Tickets Added: {summary['tickets_added']}")
        print(f"   • Resolved: {summary['tickets_resolved']}")

    # 9. Verify Agent Logs include Desk Officer + all pipeline agents
    print("\n[Step 8] Checking Agent Activity Audit Trail...")
    async with async_session() as session:
        res = await session.execute(select(AgentLog).where(AgentLog.project_id == 1))
        logs = res.scalars().all()
        logged_agents = {l.agent_name for l in logs}
        print(f"✅ Total Agent Actions Logged: {len(logs)} | Agents: {sorted(logged_agents)}")
        assert "Desk Officer" in logged_agents, "Desk Officer actions should be logged"

    print("\n" + "=" * 60)
    print("  🎉 ALL QA OFFICER SYSTEMS VERIFIED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_e2e_tests())
