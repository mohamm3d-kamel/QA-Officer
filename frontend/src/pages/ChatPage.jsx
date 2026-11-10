import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Bot, PieChart, SidebarClose, SidebarOpen, RefreshCw, ArrowUpRight } from 'lucide-react';
import useAppStore from '../store/appStore';
import api from '../services/api';
import useWebSocket from '../hooks/useWebSocket';

import ChatWindow from '../components/Chat/ChatWindow';
import InputArea from '../components/Chat/InputArea';
import AgentStatus from '../components/Chat/AgentStatus';
import SkillPanel from '../components/Chat/SkillPanel';
import AutoTestModal from '../components/Chat/AutoTestModal';

export default function ChatPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  const selectedProject = useAppStore((s) => s.selectedProject);
  const setSelectedProject = useAppStore((s) => s.setSelectedProject);
  const setSkills = useAppStore((s) => s.setSkills);
  const setBugs = useAppStore((s) => s.setBugs);
  const bugs = useAppStore((s) => s.bugs);
  const messages = useAppStore((s) => s.messages);
  const setMessages = useAppStore((s) => s.setMessages);
  const addMessage = useAppStore((s) => s.addMessage);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  const [isAutoTestOpen, setIsAutoTestOpen] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [raisingBugId, setRaisingBugId] = useState(null);

  // Initialize WebSocket for live updates
  const { isConnected } = useWebSocket();

  // Load project details, skills, and bugs
  useEffect(() => {
    async function loadProjectContext() {
      try {
        const proj = await api.getProject(projectId);
        setSelectedProject(proj);

        const projectSkills = await api.getSkills(projectId);
        setSkills(projectSkills);

        const projectBugs = await api.getBugs(projectId);
        setBugs(projectBugs);

        // Seed Desk Officer welcome greeting if empty
        if (messages.length === 0) {
          setMessages([
            {
              id: 'init-1',
              sender: 'agent',
              agent: 'Desk Officer',
              content:
                `👋 Hello **Mohamed Kamel**! I am your **Desk Officer** for **${proj.name}**.\n\n` +
                `- Let's discuss any bug details here first before starting the workflow (feel free to share symptoms, logs, steps, or ask about our built-in agents).\n` +
                `- I know all built-in agents (**Detective, Coroner, Judge, Reporter, Sheriff, Watcher, Sanity Testers**) and can assign any of them to start working on request.\n` +
                `- Say **"Create a bug report"** when you're ready to run the main workflow (**Detective → Coroner → Judge → Reporter**).\n` +
                `- **Sheriff** stays on standby until you explicitly ask to raise the ticket — and when raised, it goes into Jira **Backlog** (not To Do).`,
              timestamp: new Date().toISOString(),
            },
          ]);
        }
      } catch (err) {
        console.error('Failed to load project context:', err);
      }
    }
    if (projectId) loadProjectContext();
  }, [projectId, setSelectedProject, setSkills, setBugs]);

  const handleSendMessage = async (text, attachments = []) => {
    const historyPayload = useAppStore
      .getState()
      .messages.filter((m) => !m.isStatus && m.content)
      .slice(-14)
      .map((m) => ({
        role: m.role,
        sender: m.sender,
        agent: m.agent,
        content: m.content,
      }));

    // Add user message
    const userMsg = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      sender: 'user',
      content: text,
      attachments,
      timestamp: new Date().toISOString(),
    };
    addMessage(userMsg);
    setChatLoading(true);

    try {
      const res = await api.sendMessage({
        project_id: parseInt(projectId),
        message: text,
        attachments,
        history: historyPayload,
      });

      // Add Desk Officer response message
      addMessage({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        sender: 'agent',
        agent: res.agent || 'Desk Officer',
        content: res.response,
        pipeline_result: res.pipeline_result,
        timestamp: new Date().toISOString(),
      });

      // Refresh bugs & skills lists
      const [updatedBugs, updatedSkills] = await Promise.all([
        api.getBugs(projectId),
        api.getSkills(projectId),
      ]);
      setBugs(updatedBugs);
      setSkills(updatedSkills);
    } catch (err) {
      console.error('Failed to send message:', err);
      addMessage({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        sender: 'agent',
        agent: 'Desk Officer',
        content: `❌ Error processing request: ${err.message}`,
        timestamp: new Date().toISOString(),
      });
    } finally {
      setChatLoading(false);
    }
  };

  const handleRaiseToJira = async (bugId) => {
    setRaisingBugId(bugId);
    try {
      const res = await api.raiseBugToJira(bugId);
      const updatedBugs = await api.getBugs(projectId);
      setBugs(updatedBugs);
      const ticket = res.jira_ticket;
      addMessage({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        sender: 'agent',
        agent: 'Sheriff',
        content: `🏷️ Raised Bug #${bugId} to Jira **${ticket?.status || 'Backlog'}**: [${ticket?.key}](${ticket?.url})`,
        timestamp: new Date().toISOString(),
      });
    } catch (err) {
      console.error('Raise to Jira error:', err);
    } finally {
      setRaisingBugId(null);
    }
  };

  const handleRetestBug = async (bugId) => {
    try {
      await api.retestBug(bugId);
      const updatedBugs = await api.getBugs(projectId);
      setBugs(updatedBugs);
    } catch (err) {
      console.error('Retest error:', err);
    }
  };

  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden">
      {/* Main Chat Thread Area */}
      <div className="flex-1 flex flex-col min-w-0 bg-slate-950">
        {/* Project Header Bar */}
        <div className="h-14 px-6 border-b border-slate-800/80 bg-slate-900/60 flex items-center justify-between gap-4 backdrop-blur">
          <div className="flex items-center gap-3 truncate">
            <h2 className="text-sm font-semibold text-slate-100 truncate">
              {selectedProject?.name || 'Loading Project...'}
            </h2>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-cyan-950 text-cyan-400 border border-cyan-800">
              {selectedProject?.jira_project_key || 'QA'}
            </span>
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500' : 'bg-red-500'}`} />
              <span>{isConnected ? 'Real-time WebSocket Active' : 'Connecting...'}</span>
            </div>
          </div>

          <div className="flex items-center gap-2.5">
            <button
              onClick={() => setIsAutoTestOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-cyan-600/20 hover:bg-cyan-600 text-cyan-400 hover:text-white border border-cyan-800/50 text-xs font-semibold transition-all shadow"
            >
              <Bot className="w-4 h-4" />
              <span>🤖 Auto-Test</span>
            </button>

            <button
              onClick={() => navigate(`/summary/${projectId}`)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition-all border border-slate-700"
            >
              <PieChart className="w-4 h-4 text-cyan-400" />
              <span>Summary Report</span>
            </button>

            <button
              onClick={() => setRightSidebarOpen(!rightSidebarOpen)}
              className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              title="Toggle Pipeline Panel"
            >
              {rightSidebarOpen ? <SidebarClose className="w-4 h-4" /> : <SidebarOpen className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {/* Chat Window */}
        <div className="flex-1 overflow-hidden flex flex-col">
          <ChatWindow />
          <InputArea
            onSendMessage={handleSendMessage}
            onOpenAutoTest={() => setIsAutoTestOpen(true)}
            onOpenSummary={() => navigate(`/summary/${projectId}`)}
          />
        </div>
      </div>

      {/* Right Sidebar: Pipeline + Stored Skills + Tracked Bugs */}
      {rightSidebarOpen && (
        <div className="w-96 border-l border-slate-800 bg-slate-900/90 overflow-y-auto p-4 space-y-5 animate-in slide-in-from-right duration-200">
          {/* Agent Pipeline Stepper */}
          <AgentStatus />

          {/* Stored Skills (Requirements & Report Format) */}
          <SkillPanel projectId={projectId} />

          {/* Tracked Defects in this Project */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-lg backdrop-blur">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-slate-200">Tracked Defects ({bugs.length})</h3>
              <span className="text-[10px] text-slate-500 uppercase font-semibold">Reports & Jira</span>
            </div>

            <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
              {bugs.length === 0 ? (
                <div className="text-center py-4 text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
                  No defects logged yet. Chat with Desk Officer or say "Create a bug report".
                </div>
              ) : (
                bugs.map((bug) => (
                  <div
                    key={bug.id}
                    className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800 text-xs space-y-1.5 hover:border-slate-700 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono font-bold text-cyan-400">
                        {bug.jira_ticket_id || `Report #${bug.id}`}
                      </span>
                      <span
                        className={`text-[9px] uppercase font-semibold px-1.5 py-0.5 rounded ${
                          bug.status === 'resolved'
                            ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                            : bug.status === 'failed'
                            ? 'bg-red-950 text-red-400 border border-red-800'
                            : bug.status === 'in_qa'
                            ? 'bg-amber-950 text-amber-400 border border-amber-800'
                            : 'bg-blue-950 text-blue-400 border border-blue-800'
                        }`}
                      >
                        {bug.status}
                      </span>
                    </div>
                    <p className="font-medium text-slate-200 line-clamp-1">{bug.title}</p>
                    <div className="flex items-center justify-between gap-2 pt-1 border-t border-slate-800/60 text-[10px] text-slate-500">
                      <span>{bug.label || 'Front-End'}</span>
                      <div className="flex items-center gap-2">
                        {!bug.jira_ticket_id && bug.status !== 'resolved' && (
                          <button
                            onClick={() => handleRaiseToJira(bug.id)}
                            disabled={raisingBugId === bug.id}
                            className="text-blue-400 hover:underline font-semibold flex items-center gap-0.5 disabled:opacity-50"
                          >
                            <ArrowUpRight className="w-2.5 h-2.5" />
                            <span>{raisingBugId === bug.id ? 'Raising...' : 'Raise to Backlog'}</span>
                          </button>
                        )}
                        {bug.status !== 'resolved' && (
                          <button
                            onClick={() => handleRetestBug(bug.id)}
                            className="text-cyan-400 hover:underline font-semibold flex items-center gap-1"
                          >
                            <RefreshCw className="w-2.5 h-2.5" />
                            <span>Retest in QA</span>
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* Auto-Test Modal */}
      <AutoTestModal
        isOpen={isAutoTestOpen}
        onClose={() => setIsAutoTestOpen(false)}
        projectId={projectId}
        onComplete={async () => {
          const updatedBugs = await api.getBugs(projectId);
          setBugs(updatedBugs);
        }}
      />
    </div>
  );
}
