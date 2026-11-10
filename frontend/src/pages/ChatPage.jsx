import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Bot, PieChart, SidebarClose, SidebarOpen, RefreshCw, ArrowUpRight, RotateCcw } from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import useWebSocket from '../hooks/useWebSocket';

import ChatWindow from '../components/Chat/ChatWindow';
import InputArea from '../components/Chat/InputArea';
import AgentStatus from '../components/Chat/AgentStatus';
import SkillPanel from '../components/Chat/SkillPanel';
import AutoTestModal from '../components/Chat/AutoTestModal';
import BugDetailModal from '../components/Chat/BugDetailModal';

function buildWelcomeMessage(projectName, reporterName) {
  return {
    id: `init-${Date.now()}`,
    sender: 'agent',
    agent: 'Desk Officer',
    content:
      `👋 Hello **${reporterName || 'QA Engineer'}**! I am your **Desk Officer** for **${projectName}**.\n\n` +
      `- Let's discuss any bug details here first before starting the workflow (feel free to share symptoms, logs, steps, or ask about our built-in agents).\n` +
      `- I know all built-in agents (**Detective, Coroner, Judge, Sheriff, Watcher, Sanity Testers**) and can assign any of them to start working on request.\n` +
      `- Say **"Create a bug report"** when you're ready to run the main workflow (**Detective → Coroner → Judge**, with Detective investigating & authoring the formatted report).\n` +
      `- **Sheriff** stays on standby until you explicitly ask to raise the ticket — and when raised, it goes into Jira **Backlog** (not To Do).`,
    timestamp: new Date().toISOString(),
  };
}

export default function ChatPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  const selectedProject = useAppStore((s) => s.selectedProject);
  const setSelectedProject = useAppStore((s) => s.setSelectedProject);
  const reporterName = useAppStore((s) => s.reporterName);
  const setReporterName = useAppStore((s) => s.setReporterName);
  const setSkills = useAppStore((s) => s.setSkills);
  const setBugs = useAppStore((s) => s.setBugs);
  const removeBug = useAppStore((s) => s.removeBug);
  const bugs = useAppStore((s) => s.bugs);
  const setMessages = useAppStore((s) => s.setMessages);
  const addMessage = useAppStore((s) => s.addMessage);
  const clearProjectMessages = useAppStore((s) => s.clearProjectMessages);
  const clearAgentStatuses = useAppStore((s) => s.clearAgentStatuses);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  const [isAutoTestOpen, setIsAutoTestOpen] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [raisingBugId, setRaisingBugId] = useState(null);
  const [selectedBug, setSelectedBug] = useState(null);

  const { isConnected } = useWebSocket(undefined, projectId);

  useEffect(() => {
    let cancelled = false;
    async function loadProjectContext() {
      try {
        const [proj, projectSkills, projectBugs, settingsList] = await Promise.all([
          api.getProject(projectId),
          api.getSkills(projectId),
          api.getBugs(projectId),
          api.getSettings().catch(() => []),
        ]);
        if (cancelled) return;

        setSelectedProject(proj);
        setSkills(projectSkills);
        setBugs(projectBugs);

        let activeReporter = useAppStore.getState().reporterName;
        if (Array.isArray(settingsList)) {
          const repSetting = settingsList.find((item) => item.key === 'default_reporter');
          if (repSetting?.value) {
            activeReporter = repSetting.value;
            setReporterName(repSetting.value);
          }
        }

        const existingMsgs = useAppStore.getState().messagesByProject[String(projectId)] || [];
        if (existingMsgs.length === 0) {
          setMessages([buildWelcomeMessage(proj.name, activeReporter)], projectId);
        }
      } catch (err) {
        if (!cancelled) {
          toast.error(err.message || 'Failed to load project context');
        }
      }
    }
    if (projectId) loadProjectContext();
    return () => {
      cancelled = true;
    };
  }, [projectId, setSelectedProject, setSkills, setBugs, setMessages, setReporterName]);

  const handleSendMessage = async (text, attachments = []) => {
    const pid = String(projectId);
    const currentMsgs = useAppStore.getState().messagesByProject[pid] || [];
    const historyPayload = currentMsgs
      .filter((m) => !m.isStatus && m.content)
      .slice(-14)
      .map((m) => ({
        role: m.role,
        sender: m.sender,
        agent: m.agent,
        content: m.content,
      }));

    const userMsg = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      sender: 'user',
      content: text + (attachments.length ? `\n\n📎 ${attachments.map((a) => a.name).join(', ')}` : ''),
      attachments,
      timestamp: new Date().toISOString(),
    };
    addMessage(userMsg, projectId);
    setChatLoading(true);

    try {
      const res = await api.sendMessage({
        project_id: parseInt(projectId, 10),
        message: text,
        attachments,
        history: historyPayload,
      });

      addMessage(
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          sender: 'agent',
          agent: res.agent || 'Desk Officer',
          content: res.response,
          pipeline_result: res.pipeline_result,
          evidence: res.evidence || res.pipeline_result?.evidence,
          llmDegraded: Boolean(res.llm_degraded),
          timestamp: new Date().toISOString(),
        },
        projectId
      );

      const [updatedBugs, updatedSkills] = await Promise.all([
        api.getBugs(projectId),
        api.getSkills(projectId),
      ]);
      setBugs(updatedBugs);
      setSkills(updatedSkills);
    } catch (err) {
      toast.error(err.message || 'Failed to process message');
      addMessage(
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          sender: 'agent',
          agent: 'Desk Officer',
          content: `❌ Error processing request: ${err.message}`,
          timestamp: new Date().toISOString(),
        },
        projectId
      );
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
      toast.success(`Raised ${ticket?.key || `Bug #${bugId}`} to Jira Backlog`);
      addMessage(
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          sender: 'agent',
          agent: 'Sheriff',
          content: `🏷️ Raised Bug #${bugId} to Jira **${ticket?.status || 'Backlog'}**: [${ticket?.key}](${ticket?.url})`,
          timestamp: new Date().toISOString(),
        },
        projectId
      );
    } catch (err) {
      toast.error(err.message || 'Failed to raise ticket to Jira');
    } finally {
      setRaisingBugId(null);
    }
  };

  const handleRetestBug = async (bugId) => {
    try {
      const res = await api.retestBug(bugId);
      const updatedBugs = await api.getBugs(projectId);
      setBugs(updatedBugs);
      const newStatus = res?.retest?.new_status || 'Updated';
      toast.success(`Bug #${bugId} retest finished: ${newStatus}`);
    } catch (err) {
      toast.error(err.message || 'Retest failed');
    }
  };

  const handleDeleteBug = async (bugId) => {
    try {
      await api.deleteBug(bugId);
      removeBug(bugId);
      toast.success(`Deleted Bug Report #${bugId}`);
    } catch (err) {
      toast.error(err.message || 'Failed to delete bug report');
    }
  };

  const handleClearChat = () => {
    clearProjectMessages(projectId);
    clearAgentStatuses(projectId);
    setMessages(
      [buildWelcomeMessage(selectedProject?.name || 'this project', reporterName)],
      projectId
    );
    toast.success('Started fresh chat session');
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
            <div className="hidden sm:flex items-center gap-1.5 text-xs text-slate-500">
              <span className={`w-2 h-2 rounded-full ${isConnected ? 'bg-emerald-500' : 'bg-red-500'}`} />
              <span>{isConnected ? 'Live WebSocket' : 'Reconnecting...'}</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleClearChat}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-colors border border-slate-700/70"
              title="Clear Chat History"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span className="hidden md:inline">New Chat</span>
            </button>

            <button
              onClick={() => setIsAutoTestOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-cyan-600/20 hover:bg-cyan-600 text-cyan-400 hover:text-white border border-cyan-800/50 text-xs font-semibold transition-all shadow"
            >
              <Bot className="w-4 h-4" />
              <span>Auto-Test</span>
            </button>

            <button
              onClick={() => navigate(`/summary/${projectId}`)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition-all border border-slate-700"
            >
              <PieChart className="w-4 h-4 text-cyan-400" />
              <span className="hidden sm:inline">Summary Report</span>
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
          <ChatWindow projectId={projectId} onQuickSend={(q) => handleSendMessage(q, [])} />
          <InputArea
            projectId={projectId}
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
          <AgentStatus projectId={projectId} />

          {/* Stored Skills (Requirements & Report Format) */}
          <SkillPanel projectId={projectId} />

          {/* Tracked Defects in this Project */}
          <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-lg backdrop-blur">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-slate-200">Tracked Defects ({bugs.length})</h3>
              <span className="text-[10px] text-slate-500 uppercase font-semibold">Click to Inspect</span>
            </div>

            <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
              {bugs.length === 0 ? (
                <div className="text-center py-4 text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
                  No defects logged yet. Chat with Desk Officer or say &quot;Create a bug report&quot;.
                </div>
              ) : (
                bugs.map((bug) => (
                  <div
                    key={bug.id}
                    onClick={() => setSelectedBug(bug)}
                    className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800 text-xs space-y-1.5 hover:border-cyan-500/40 cursor-pointer transition-colors"
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
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRaiseToJira(bug.id);
                            }}
                            disabled={raisingBugId === bug.id}
                            className="text-blue-400 hover:underline font-semibold flex items-center gap-0.5 disabled:opacity-50"
                          >
                            <ArrowUpRight className="w-2.5 h-2.5" />
                            <span>{raisingBugId === bug.id ? 'Raising...' : 'Raise to Backlog'}</span>
                          </button>
                        )}
                        {bug.status !== 'resolved' && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRetestBug(bug.id);
                            }}
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

      {/* Bug Report & Coroner Evidence Inspector */}
      <BugDetailModal
        bug={selectedBug}
        isOpen={Boolean(selectedBug)}
        onClose={() => setSelectedBug(null)}
        onRaiseToJira={handleRaiseToJira}
        onRetest={handleRetestBug}
        onDelete={handleDeleteBug}
      />
    </div>
  );
}
