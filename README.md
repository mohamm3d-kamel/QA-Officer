# 🛡️ QA Officer — Autonomous Multi-Agent Software Testing Assistant

An AI-powered software testing platform built for modern QA teams. It coordinates a specialized multi-agent pipeline (guided conversationally by **Desk Officer**) to investigate defects, capture live Playwright visual & runtime evidence, independently verify root causes, author bug reports matching your project's **Report Format**, manage Jira Cloud lifecycles, monitor Jira updates & AI agent failures, and run sanity tests.

---

## 🌟 Key Features

1. **Persistent Business Logic, Project Assets & Report Formats**
   - Upload business requirements, user stories, acceptance criteria (including `.txt`, `.md`, `.json`, `.csv`, `.pdf`, `.docx`), or configure a dedicated **Report Format** template per project so every bug report follows your exact structure as-is.
   - Connect optional repositories, documentation URLs, or local codebases via the **Project Asset Hub** so investigations stay grounded in your real project.

2. **Multi-Agent Defect Pipeline (Coordinated by Desk Officer)**
   - **🎖️ Desk Officer (Chat Coordinator & Dispatcher)**: Chats with you interactively (supporting screenshots, PDFs, Office docs, logs, and HAR captures), asks clarifying questions when critical bug details are missing, assigns individual agents or multi-agent collaboration plans on demand, and launches the full pipeline when requested.
   - **🔍 Detective (Root Cause Investigator)**: Traces bug symptoms, runtime telemetry, logs, code context, and requirements to identify root cause, reproduction steps, severity, label, and affected module — plus retests fixes and runs autonomous discovery.
   - **📸 Coroner (Evidence Collector)**: Runs automated Playwright browser sessions (Chromium / Firefox / WebKit) to capture screenshots, video recordings, console/network errors, and diagnostic snapshots during bug reproduction and failed QA retests.
   - **⚖️ Judge (Senior Verifier)**: Independently verifies Detective's root cause, Coroner's runtime telemetry/evidence, and attached files before approving the bug report.
   - **📝 Reporter (Bug Report Author)**: Authors clean, verified Markdown bug reports incorporating Detective findings, Judge verdicts, and Coroner evidence while strictly following your project's configured **Report Format** template as-is.
   - **🏷️ Sheriff (Jira Commander)**: Creates Jira Cloud tickets in the `Backlog` (never `To Do`) **only when explicitly requested**, attaching Coroner evidence directly to Jira, formatting ADF bodies, selecting priority & label (`Back-End`, `Front-End`, `Mobile`), and assigning the configured reporter.
   - **👁️ Watcher (Jira Update & Agent Failure Sentinel)**: Monitors Jira ticket transitions/updates (`In QA` triggers Detective retest -> `Ready To Deploy` or `Failed QA` with Coroner evidence) and watches all AI agents. External **Slack** and **Email (SMTP)** alerts fire strictly for **Jira updates** and **AI agent failures**, while internal module/log telemetry stays visible in the UI Audit Logs & Timeline.
   - **🧪 Sanity Testers (Regression Checker)**: Evaluates adjacent and affected modules of resolved bugs to verify fixes introduce zero regressions.

3. **📊 Executive Summary & Dashboard**
   - Live KPIs, module/feature test coverage percentage, ticket counts (`Backlog` / `In QA` / `Failed QA` / `Ready To Deploy`), interactive bug detail inspection modal, and print-ready summary reports.

4. **📋 Full System Audit Logs & Live Timeline**
   - Filterable activity table and real-time WebSocket timeline tracking every agent action, LLM call status (`success`, `failed`, `degraded`, `running`), and Jira transition.

5. **🤖 Autonomous Bug Discovery (Auto-Test)**
   - Scan stored requirements and probe live routes with duplicate filtering to automatically detect discrepancies and edge-case failures.

---

## ⚙️ Configuration (Settings Page)

Open the **Settings** tab (or jump directly to **Email Alerts (SMTP)** via `/settings?section=email-alerts#email-alerts` and select a quick preset) to configure:
- **LLM Engine**: OpenAI, Google Gemini / Vertex AI, Anthropic Claude, or Ollama (Local) — API keys are masked in API responses (`••••••••1234`).
- **Jira Cloud**: Base URL (`https://your-domain.atlassian.net`), email, API token, and **Default Reporter**.
- **Watcher & Notifications**: Polling interval, **Slack Webhook**, and **Email Alerts (SMTP)** with one-click presets (`Gmail`, `Microsoft 365`, `Zoho Mail`, `SendGrid`). External Slack/Email notifications report **Jira updates** and **AI agent failures** only.
- **Browser Automation**: Multi-browser selection (Chromium, Firefox, WebKit) and Headless mode.

---

## 📂 Project Structure

```
QA-Officer/
├── backend/
│   ├── agents/               # Desk Officer, Detective, Coroner, Judge, Reporter, Sheriff, Watcher, Sanity Testers
│   ├── models/               # SQLAlchemy Async SQLite Models (Project, Skill, BugReport, Evidence, ChatMessage, AgentLog, Settings)
│   ├── routers/              # FastAPI Routers (Projects, Skills, Bugs, Chat, Logs, Settings, Reports, WebSocket)
│   ├── services/             # LLMRouter, JiraClient, BrowserEngine, AttachmentProcessor, ProjectAssetHub, NotificationService, WatcherBus
│   ├── storage/              # Database file (qa_officer.db), evidence media, uploaded skills/chat files
│   ├── main.py               # FastAPI application entry point with background Watcher loop & CORS config
│   ├── test_api.py           # HTTP route & integration test suite
│   └── test_pipeline.py      # Automated E2E pipeline verification test suite
├── frontend/
│   ├── src/
│   │   ├── components/       # Chat, Layout, Logs, Projects, Reports, Settings, BugDetailModal, ErrorBoundary
│   │   ├── pages/            # DashboardPage, ChatPage, ProjectsPage, LogsPage, SummaryPage, SettingsPage
│   │   ├── store/            # Zustand state management
│   │   ├── services/         # API Client fetch wrapper
│   │   └── hooks/            # WebSocket hook
│   ├── package.json
│   ├── vite.config.js
│   └── tailwind.config.js
└── README.md
```
