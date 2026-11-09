# 🛡️ QA Officer — Autonomous Multi-Agent Software Testing Assistant

An AI-powered software testing platform built for modern QA teams. It coordinates a specialized pipeline of autonomous AI agents to investigate root causes, capture visual evidence, verify defects, author formatted reports, manage Jira Cloud lifecycles, and run sanity tests.

---

## 🌟 Key Features

1. **Persistent Business Logic & Skill Storage**
   - Upload business requirements, user stories, acceptance criteria, or your exact bug report markdown structure.
   - All instructions are permanently remembered as **Skills** to eliminate repetitive prompting.

2. **Multi-Agent Defect Pipeline**
   - **🔍 Detective (Investigator)**: Analyzes bugs, traces logs/code/DOM, identifies root cause, and reproduces defects.
   - **📸 Journalist (Evidence Collector)**: Runs automated Playwright browser session during Detective's second pass to capture full video recordings and high-res screenshots.
   - **⚖️ Judge (Senior Verifier)**: Independently re-investigates and confirms the true root cause before any report is finalized.
   - **📝 Reporter (Author)**: Automatically crafts pristine bug reports matching your custom template.
   - **🏷️ Sheriff (Jira Commander)**: Creates Jira Cloud tickets in the `To Do` backlog with evidence attachments, priority selection (`lowest` to `highest`), label classification (`Back-End`, `Front-End`, `Mobile`), and assigned reporter (`Mohamed Kamel`).
   - **👁️ Watcher (Jira Sentinel)**: Periodically monitors tickets. When a ticket moves to `In QA`, it prompts Detective to retest. If resolved, it transitions to `Ready To Deploy`. If unresolved, it moves to `Failed QA` with a detailed comment and failure evidence.
   - **🧪 Sanity Testers (Regression Checker)**: Automatically evaluates adjacent and affected modules of resolved bugs to guarantee fixes introduce zero side-effects.

3. **📊 Executive Summary Report**
   - One-click summary detailing:
     - Module/feature test coverage percentage
     - Total tickets added
     - Tickets resolved (`Ready To Deploy`)
     - Tickets failed in QA
     - Tickets under review (`In QA`)
     - Print-ready and exportable layout

4. **📋 Audit Logs & Timeline**
   - Filterable activity table and visual timeline tracking every agent action, LLM input/output, and Jira status transition.

5. **🤖 Autonomous Bug Discovery (Auto-Test)**
   - Scan all stored requirements or target specific modules to automatically detect discrepancies, edge case failures, and security bounds.

---

## 🚀 Quick Start

### 1. Launch QA Officer with One Command
```bash
cd "/home/mohamed/QA Officer"
./run_qa_officer.sh
```

This starts:
- **Frontend App**: [http://localhost:5173](http://localhost:5173)
- **Backend API**: [http://localhost:8000](http://localhost:8000)
- **Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## ⚙️ Configuration (Settings Page)

Open the **Settings** tab in the app to configure:
- **LLM Engine**: Switch between OpenAI (GPT-4 / GPT-4o), Google Gemini API, Anthropic Claude, or Ollama (Local).
- **Jira Cloud**: Provide Jira Base URL (`https://your-domain.atlassian.net`), email, and API token.
- **Default Reporter**: Pre-set to `Mohamed Kamel`.
- **Watcher Polling Interval**: Configurable (default: `60 minutes`).
- **Notifications**: Enable instant Email and Slack Webhook alerts when tickets change status.
- **Browser Automation**: Multi-browser support (Chromium, Firefox, WebKit, Headless mode).

---

## 📂 Project Structure

```
QA Officer/
├── backend/
│   ├── agents/               # Detective, Journalist, Judge, Reporter, Sheriff, Watcher, Sanity Testers
│   ├── models/               # SQLAlchemy SQLite Models (Project, Skill, BugReport, Evidence, AgentLog)
│   ├── routers/              # FastAPI Routers (Projects, Skills, Bugs, Chat, Logs, Settings, Reports, WebSocket)
│   ├── services/             # LLMRouter, JiraClient, BrowserEngine, NotificationService, EvidenceCapture
│   ├── storage/              # Database file (qa_officer.db) and evidence media
│   ├── main.py               # FastAPI application entry point with background Watcher loop
│   └── test_pipeline.py      # Automated E2E verification test suite
├── frontend/
│   ├── src/
│   │   ├── components/       # Chat, Layout, Logs, Projects, Reports, Settings
│   │   ├── pages/            # Dashboard, ChatPage, ProjectsPage, LogsPage, SummaryPage, SettingsPage
│   │   ├── store/            # Zustand state management
│   │   ├── services/         # API Client fetch wrapper
│   │   └── hooks/            # WebSocket and Data Fetching hooks
│   ├── package.json
│   ├── vite.config.js
│   └── tailwind.config.js
├── run_qa_officer.sh         # Complete app startup runner
└── README.md
```

