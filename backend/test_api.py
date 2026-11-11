"""FastAPI HTTP Route Integration Test Suite for QA Officer Backend."""
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

# Isolate API test DB from the live database BEFORE importing backend modules
TEST_API_DB = Path(tempfile.gettempdir()) / "qa_officer_api_test.db"
if TEST_API_DB.exists():
    TEST_API_DB.unlink()
os.environ["QA_OFFICER_DB_PATH"] = str(TEST_API_DB)

sys.path.insert(0, str(Path(__file__).parent / "site-packages"))
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import httpx
from sqlalchemy import select, text

import config
from database import init_db, async_session, engine
from main import app, seed_initial_data
from models.settings import Settings
from models.agent_log import AgentLog


def _build_synthetic_docx(paragraphs: list[str]) -> bytes:
    """Create a minimal valid .docx ZIP archive containing word/document.xml."""
    p_xml = "".join(
        f"<w:p><w:r><w:t>{text_val}</w:t></w:r></w:p>" for text_val in paragraphs
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{p_xml}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))
    return buf.getvalue()


async def run_api_tests():
    print("=" * 60)
    print("  🌐 QA OFFICER — HTTP API INTEGRATION TEST SUITE")
    print("=" * 60)

    before_evidence = set(config.EVIDENCE_DIR.glob("*"))

    try:
        await init_db()
        await seed_initial_data()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. Health Check
            res = await client.get("/api/health")
            assert res.status_code == 200
            assert res.json()["status"] == "ok"
            print("✅ [1/9] GET /api/health -> 200 OK")

            # 2. Settings secret masking & preservation on PUT
            put_secret = await client.put(
                "/api/settings",
                json={
                    "llm_api_key": "sk-super-secret-9876",
                    "smtp_host": "smtp.initial.local",
                    "default_reporter": "Mohamed Kamel",
                },
            )
            assert put_secret.status_code == 200

            get_masked = await client.get("/api/settings")
            assert get_masked.status_code == 200
            settings_map = {item["key"]: item["value"] for item in get_masked.json()}
            assert settings_map["llm_api_key"] == "••••••••9876", f"Expected masked key, got {settings_map['llm_api_key']}"
            assert settings_map["default_reporter"] == "Mohamed Kamel"

            # Send masked placeholder back with another field changed; raw DB secret must remain intact
            put_preserve = await client.put(
                "/api/settings",
                json={
                    "llm_api_key": settings_map["llm_api_key"],
                    "smtp_host": "smtp.updated.local",
                },
            )
            assert put_preserve.status_code == 200
            async with async_session() as session:
                db_key = (await session.execute(select(Settings).where(Settings.key == "llm_api_key"))).scalar_one().value
                db_smtp = (await session.execute(select(Settings).where(Settings.key == "smtp_host"))).scalar_one().value
                assert db_key == "sk-super-secret-9876", f"Secret overwritten by mask! Got {db_key}"
                assert db_smtp == "smtp.updated.local"
            print("✅ [2/9] Settings secret masking & mask-safe PUT preservation verified")

            # 3. Project CRUD
            res_proj = await client.post(
                "/api/projects",
                json={
                    "name": "API Integration Project",
                    "jira_project_key": "APIT",
                    "app_url": "",
                    "description": "Isolated project for HTTP route testing",
                },
            )
            assert res_proj.status_code == 200
            proj = res_proj.json()
            proj_id = proj["id"]
            assert proj["jira_project_key"] == "APIT"

            res_upd = await client.put(f"/api/projects/{proj_id}", json={"description": "Updated HTTP test project"})
            assert res_upd.status_code == 200
            assert res_upd.json()["description"] == "Updated HTTP test project"
            print(f"✅ [3/9] Project CRUD verified (project_id={proj_id})")

            # 4. Skills upload (.docx extraction + 5 MB size cap)
            docx_bytes = _build_synthetic_docx([
                "Authentication Specification",
                "Password reset tokens expire cleanly after 15 minutes.",
            ])
            res_docx = await client.post(
                f"/api/projects/{proj_id}/skills/upload",
                data={"type": "requirement", "title": "Auth Spec Docx"},
                files={"file": ("auth_spec.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
            assert res_docx.status_code == 200, res_docx.text
            skill_data = res_docx.json()
            assert "Password reset tokens expire cleanly after 15 minutes." in skill_data["content"]

            oversized_bytes = b"A" * (5 * 1024 * 1024 + 1024)
            res_big = await client.post(
                f"/api/projects/{proj_id}/skills/upload",
                data={"type": "requirement", "title": "Too Large"},
                files={"file": ("huge.txt", oversized_bytes, "text/plain")},
            )
            assert res_big.status_code == 413, f"Expected 413 Payload Too Large, got {res_big.status_code}"
            print("✅ [4/9] Skill .docx extraction & 5 MB upload limit (413) verified")

            # 5. Chat endpoint -> conversational triage & main workflow -> evidence + llm_degraded
            res_chat = await client.post(
                "/api/chat",
                json={
                    "project_id": proj_id,
                    "message": "Login form submits twice when pressing Enter",
                    "history": [],
                },
            )
            assert res_chat.status_code == 200
            chat_json = res_chat.json()
            assert chat_json["type"] == "chat"
            assert "llm_degraded" in chat_json

            res_pipe = await client.post(
                "/api/chat",
                json={
                    "project_id": proj_id,
                    "message": "Create a bug report",
                    "history": [
                        {"sender": "user", "content": "Login form submits twice when pressing Enter"},
                        {"sender": "agent", "agent": "Desk Officer", "content": chat_json["response"]},
                    ],
                },
            )
            assert res_pipe.status_code == 200, res_pipe.text
            pipe_json = res_pipe.json()
            assert pipe_json["type"] == "pipeline_complete"
            assert pipe_json["llm_degraded"] is True
            ev_list = pipe_json.get("evidence", [])
            assert len(ev_list) >= 1 and ev_list[0].get("id"), f"Expected evidence with id, got {ev_list}"
            bug_id = pipe_json["pipeline_result"]["bug_id"]
            print(f"✅ [5/9] POST /api/chat pipeline response includes evidence (id={ev_list[0]['id']}) & llm_degraded=True")

            # 6. Evidence serving + path traversal protection
            ev_id = ev_list[0]["id"]
            ev_name = ev_list[0]["file_name"]
            res_ev = await client.get(f"/api/evidence/{ev_id}/file")
            assert res_ev.status_code == 200
            assert res_ev.headers.get("content-type", "").startswith("image/png")

            res_raw = await client.get(f"/api/evidence/raw/{ev_name}")
            assert res_raw.status_code == 200

            res_trav = await client.get("/api/evidence/raw/..%2F..%2Fmain.py")
            assert res_trav.status_code in (403, 404), f"Traversal should be blocked, got {res_trav.status_code}"
            print("✅ [6/9] Evidence file serving & path traversal protection verified")

            # 7. Dashboard & Summary reports endpoints
            res_dash = await client.get("/api/reports/dashboard")
            assert res_dash.status_code == 200
            dash = res_dash.json()
            assert dash["total_projects"] >= 2
            assert dash["total_bugs"] >= 1
            assert dash["confirmed_bugs"] >= 1

            res_sum = await client.get(f"/api/reports/summary/{proj_id}")
            assert res_sum.status_code == 200
            sum_json = res_sum.json()
            assert sum_json["project_id"] == proj_id
            assert sum_json["tickets_added"] == 1
            print("✅ [7/9] Dashboard KPI & project summary report endpoints verified")

            # 8. Delete Bug under PRAGMA foreign_keys=ON
            async with async_session() as session:
                fk_status = (await session.execute(text("PRAGMA foreign_keys"))).scalar()
                assert fk_status == 1, f"Expected SQLite PRAGMA foreign_keys=1, got {fk_status}"

            res_del_bug = await client.delete(f"/api/bugs/{bug_id}")
            assert res_del_bug.status_code == 200, res_del_bug.text
            res_get_bug = await client.get(f"/api/bugs/{bug_id}")
            assert res_get_bug.status_code == 404
            print(f"✅ [8/9] DELETE /api/bugs/{bug_id} cascaded cleanly with PRAGMA foreign_keys=ON")

            # 9. Create second bug, then DELETE /api/projects/{id} and verify cascade + AgentLog cleanup
            res_pipe2 = await client.post(
                "/api/chat",
                json={
                    "project_id": proj_id,
                    "message": "Create a bug report",
                    "history": [{"sender": "user", "content": "Profile avatar fails to save"}],
                },
            )
            assert res_pipe2.status_code == 200

            res_del_proj = await client.delete(f"/api/projects/{proj_id}")
            assert res_del_proj.status_code == 200, res_del_proj.text
            res_get_proj = await client.get(f"/api/projects/{proj_id}")
            assert res_get_proj.status_code == 404

            async with async_session() as session:
                leftover_logs = (
                    await session.execute(select(AgentLog).where(AgentLog.project_id == proj_id))
                ).scalars().all()
                assert len(leftover_logs) == 0, f"Expected 0 orphaned AgentLog rows, found {len(leftover_logs)}"
            print(f"✅ [9/9] DELETE /api/projects/{proj_id} cascaded skills/bugs & purged AgentLog rows")
    finally:
        await engine.dispose()
        # Cleanup synthetic evidence files & temp DB created during this test run
        after_evidence = set(config.EVIDENCE_DIR.glob("*"))
        created_evidence_files = [p for p in (after_evidence - before_evidence) if p.is_file()]
        for p in created_evidence_files:
            p.unlink(missing_ok=True)
        if TEST_API_DB.exists():
            TEST_API_DB.unlink()

    print("\n" + "=" * 60)
    print("  🎉 ALL FASTAPI HTTP INTEGRATION TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_api_tests())
