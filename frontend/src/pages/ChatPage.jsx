import React, { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Bot,
  PieChart,
  SidebarClose,
  SidebarOpen,
  RefreshCw,
  ArrowUpRight,
  Sparkles,
  AlertCircle,
  RotateCcw,
  Upload,
  SlidersHorizontal,
} from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import useWebSocket from '../hooks/useWebSocket';

import ChatWindow from '../components/Chat/ChatWindow';
import InputArea from '../components/Chat/InputArea';
import AgentStatus from '../components/Chat/AgentStatus';
import SkillPanel from '../components/Chat/SkillPanel';
import AutoTestModal from '../components/Chat/AutoTestModal';
import ProjectForm from '../components/Projects/ProjectForm';
import BugDetailModal from '../components/Chat/BugDetailModal';

function buildWelcomeMessage(projectName, reporterName) {
  return {
    id: `init-${Date.now()}`,
    sender: 'agent',
    agent: 'Desk Officer',
    content:
      `👋 Hello **${reporterName || 'Mohamed Kamel'}**! I am your **Desk Officer** AI assistant for **${projectName}**.\n\n` +
      `- I have live access to all project assets: **Jira Cloud boards & issues, stored skills & Report Format templates, bug reports, visual evidence, agent logs, and target app inspection**.\n` +
      `- You can **attach, drag-and-drop, or paste (\`Ctrl+V\`) screenshots, images, PDFs, Word/Excel/PowerPoint docs, HAR network traces, logs, JSON, CSV, or code files** — I will analyze them visually & structurally.\n` +
      `- I know all 7 built-in agents (**Detective, Coroner, Judge, Reporter, Sheriff, Watcher, Sanity Testers**) and can assign any individual agent or **organize a custom multi-agent sequence** whenever you ask.\n` +
      `- Say **"Create a bug report"** when you're ready for the main workflow (**Detective → Coroner → Judge → Reporter**).\n` +
      `- **Sheriff** stays on standby until you explicitly ask to raise the ticket — and when raised, it always lands in Jira **Backlog**.`,
    suggested_actions: [
      'Check my Jira In QA list',
      'Show all project assets and built-in agents',
      'Organize Detective, Coroner, and Reporter for a bug',
      'Run Auto-Test discovery',
    ],
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
  const finalizeStreamMessage = useAppStore((s) => s.finalizeStreamMessage);
  const clearProjectMessages = useAppStore((s) => s.clearProjectMessages);
  const clearAgentStatuses = useAppStore((s) => s.clearAgentStatuses);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  const [isAutoTestOpen, setIsAutoTestOpen] = useState(false);
  const [isProjectConfigOpen, setIsProjectConfigOpen] = useState(false);
  const [rightSidebarOpen, setRightSidebarOpen] = useState(true);
  const [raisingBugId, setRaisingBugId] = useState(null);
  const [llmStatus, setLlmStatus] = useState(null);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const [droppedFiles, setDroppedFiles] = useState([]);
  const [selectedBug, setSelectedBug] = useState(null);

  const { isConnected } = useWebSocket(undefined, projectId);

  const refreshLlmStatus = useCallback(async () => {
    try {
      const status = await api.getLlmStatus();
      setLlmStatus(status);
    } catch (err) {
      console.error('Failed to check LLM status:', err);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadProjectContext() {
      try {
        const [proj, projectSkills, projectBugs, history, aiStatus, settingsList] =
          await Promise.all([
            api.getProject(projectId),
            api.getSkills(projectId),
            api.getBugs(projectId),
            api.getChatHistory(projectId).catch(() => null),
            api.getLlmStatus().catch(() => null),
            api.getSettings().catch(() => []),
          ]);
        if (cancelled) return;

        setSelectedProject(proj);
        setSkills(projectSkills);
        setBugs(projectBugs);
        if (aiStatus) setLlmStatus(aiStatus);

        let activeReporter = useAppStore.getState().reporterName;
        if (Array.isArray(settingsList)) {
          const repSetting = settingsList.find((item) => item.key === 'default_reporter');
          if (repSetting?.value) {
            activeReporter = repSetting.value;
            setReporterName(repSetting.value);
          }
        }

        if (Array.isArray(history) && history.length > 0) {
          setMessages(history, projectId);
        } else {
          const existingMsgs = useAppStore.getState().messagesByProject[String(projectId)] || [];
          if (existingMsgs.length === 0 || history !== null) {
            setMessages([buildWelcomeMessage(proj.name, activeReporter)], projectId);
          }
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
    const cleanText = (text || '').trim();
    if (!cleanText && attachments.length === 0) return;

    const pid = String(projectId);
    const currentMsgs = useAppStore.getState().messagesByProject[pid] || [];
    const historyPayload = currentMsgs
      .filter(
        (m) =>
          !m.isStatus &&
          !m.isStreaming &&
          (m.content || (Array.isArray(m.attachments) && m.attachments.length > 0))
      )
      .slice(-16)
      .map((m) => ({
        role: m.role,
        sender: m.sender,
        agent: m.agent,
        content: m.content || '',
        ...(Array.isArray(m.attachments) && m.attachments.length > 0
          ? {
              attachments: m.attachments.map((a) => ({
                stored_name: a.stored_name,
                name: a.name,
                mime_type: a.mime_type,
                size_bytes: a.size_bytes,
                is_image: a.is_image,
                is_pdf: a.is_pdf,
                width: a.width,
                height: a.height,
                page_count: a.page_count,
                url: a.url,
                summary: a.summary,
                highlights: a.highlights,
                extracted_text: a.extracted_text,
              })),
            }
          : {}),
      }));

    const userMsg = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      sender: 'user',
      content: cleanText,
      attachments,
      timestamp: new Date().toISOString(),
    };
    addMessage(userMsg, projectId);
    setChatLoading(true);

    const streamId = `stream-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

    try {
      const res = await api.sendMessage({
        project_id: parseInt(projectId, 10),
        message: cleanText,
        attachments,
        history: historyPayload,
        stream_id: streamId,
      });

      finalizeStreamMessage(
        streamId,
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          streamId,
          sender: 'agent',
          agent: res.agent || 'Desk Officer',
          content: res.response,
          pipeline_result: res.pipeline_result,
          evidence: res.evidence || res.pipeline_result?.evidence,
          suggested_actions: res.suggested_actions,
          llm_mode: res.llm_mode,
          llm_notice: res.llm_notice,
          llmDegraded: Boolean(res.llm_degraded),
          timestamp: new Date().toISOString(),
        },
        projectId
      );

      const [updatedProject, updatedBugs, updatedSkills] = await Promise.all([
        api.getProject(projectId),
        api.getBugs(projectId),
        api.getSkills(projectId),
      ]);
      setSelectedProject(updatedProject);
      setBugs(updatedBugs);
      setSkills(updatedSkills);
      refreshLlmStatus();
    } catch (err) {
      toast.error(err.message || 'Failed to process message');
      finalizeStreamMessage(
        streamId,
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

  const handleClearChat = async () => {
    try {
      await api.clearChatHistory(projectId);
      clearProjectMessages(projectId);
      clearAgentStatuses(projectId);
      setMessages(
        [buildWelcomeMessage(selectedProject?.name || 'this project', reporterName)],
        projectId
      );
      toast.success('Started a fresh Desk Officer chat');
    } catch {
      toast.error('Could not clear chat history');
    }
  };

  const handleRaiseToJira = async (bugId) => {
    setRaisingBugId(bugId);
    try {
      const res = await api.raiseBugToJira(bugId);
      const updatedBugs = await api.getBugs(projectId);
      setBugs(updatedBugs);
      const ticket = res.jira_ticket;
      const attachedFiles = ticket?.evidence_attached || [];
      const attachedCount = ticket?.attached_count ?? attachedFiles.length;
      const evSummary =
        attachedCount > 0
          ? ` with ${attachedCount} evidence file(s)${attachedFiles.length ? ` (${attachedFiles.join(', ')})` : ''}`
          : '';
      const syncDetail = ticket?.newly_attached?.length
        ? ` — synced ${ticket.newly_attached.length} missing file(s)`
        : '';
      const warnDetail = ticket?.attachment_warning ? `\n⚠️ ${ticket.attachment_warning}` : '';
      toast.success(`Raised Bug #${bugId} -> ${ticket?.key}${evSummary}`);
      if (ticket?.attachment_warning) {
        toast.error(ticket.attachment_warning);
      }
      addMessage(
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          sender: 'agent',
          agent: 'Sheriff',
          content: `🏷️ Raised Bug #${bugId} to Jira **${ticket?.status || 'Backlog'}**: [${ticket?.key}](${ticket?.url})${evSummary}${syncDetail}${warnDetail}`,
          timestamp: new Date().toISOString(),
        },
        projectId
      );
    } catch (err) {
      console.error('Raise to Jira error:', err);
      toast.error(err?.message || `Failed to raise Bug #${bugId} to Jira`);
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
            {llmStatus && (
              <button
                onClick={() => navigate('/settings')}
                title={llmStatus.configured ? 'Live LLM Engine Active' : llmStatus.issue || 'Configure LLM API key or Ollama'}
                className={`hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium border transition-colors ${
                  llmStatus.configured
                    ? 'bg-emerald-950/60 border-emerald-800/60 text-emerald-300 hover:bg-emerald-950'
                    : 'bg-amber-950/60 border-amber-800/60 text-amber-300 hover:bg-amber-900/60'
                }`}
              >
                {llmStatus.configured ? (
                  <Sparkles className="w-3 h-3" />
                ) : (
                  <AlertCircle className="w-3 h-3" />
                )}
                <span>
                  {llmStatus.configured
                    ? `${llmStatus.provider} · ${llmStatus.model}`
                    : 'AI Offline · Setup LLM'}
                </span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setIsProjectConfigOpen(true)}
              title={
                selectedProject?.report_format
                  ? 'Project Configuration (Custom Report Format active)'
                  : 'Project Configuration & Report Format'
              }
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-all border border-slate-700"
            >
              <SlidersHorizontal className="w-3.5 h-3.5 text-emerald-400" />
              <span className="hidden lg:inline">Project Config</span>
              {selectedProject?.report_format && (
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              )}
            </button>

            <button
              onClick={handleClearChat}
              title="Start New Conversation"
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 text-xs font-medium transition-all border border-slate-700"
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

        {/* Helpful AI Setup Banner when no LLM API key is configured */}
        {llmStatus && !llmStatus.configured && (
          <div className="px-6 py-2 bg-amber-950/40 border-b border-amber-800/40 flex items-center justify-between gap-3 text-xs text-amber-200">
            <span>
              <strong>Offline Triage Mode:</strong> Add an OpenAI, Gemini, Anthropic, OpenRouter key or enable local Ollama in Settings for full generative AI chat.
            </span>
            <button
              onClick={() => navigate('/settings')}
              className="px-2.5 py-1 rounded-lg bg-amber-500/20 hover:bg-amber-500/30 border border-amber-600/40 text-amber-200 font-semibold whitespace-nowrap"
            >
              Open Settings →
            </button>
          </div>
        )}

        {/* Chat Window */}
        <div
          className="flex-1 overflow-hidden flex flex-col relative"
          onDragOver={(e) => {
            if (e.dataTransfer?.types?.includes('Files')) {
              e.preventDefault();
              setIsDraggingFile(true);
            }
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) {
              setIsDraggingFile(false);
            }
          }}
          onDrop={(e) => {
            if (e.dataTransfer?.files?.length) {
              e.preventDefault();
              setIsDraggingFile(false);
              setDroppedFiles(Array.from(e.dataTransfer.files));
            }
          }}
        >
          {isDraggingFile && (
            <div className="absolute inset-3 z-20 rounded-2xl border-2 border-dashed border-cyan-400 bg-slate-950/90 backdrop-blur-sm flex flex-col items-center justify-center gap-3 pointer-events-none">
              <div className="w-14 h-14 rounded-2xl bg-cyan-500/15 border border-cyan-400/40 flex items-center justify-center">
                <Upload className="w-7 h-7 text-cyan-400" />
              </div>
              <div className="text-sm font-semibold text-cyan-200">
                Drop images, screenshots, PDFs, Office docs, HAR captures, or log files
              </div>
              <div className="text-xs text-slate-400">
                Desk Officer will analyze them automatically (up to 20 MB per file)
              </div>
            </div>
          )}
          <ChatWindow
            projectId={projectId}
            onSuggestionClick={(suggested) => handleSendMessage(suggested, [])}
          />
          <InputArea
            projectId={projectId}
            onSendMessage={handleSendMessage}
            onOpenAutoTest={() => setIsAutoTestOpen(true)}
            onOpenSummary={() => navigate(`/summary/${projectId}`)}
            droppedFiles={droppedFiles}
            onClearDroppedFiles={() => setDroppedFiles([])}
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
                bugs.map((bug) => {
                  const targetProjKey = (selectedProject?.jira_project_key || 'QA').toUpperCase();
                  const hasRealTargetTicket =
                    Boolean(bug.jira_ticket_id) &&
                    new RegExp(`^${targetProjKey}-[1-9]\\d*$`).test(String(bug.jira_ticket_id).toUpperCase());
                  return (
                    <div
                      key={bug.id}
                      onClick={() => setSelectedBug(bug)}
                      className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800 text-xs space-y-1.5 hover:border-cyan-500/40 cursor-pointer transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono font-bold text-cyan-400">
                          {bug.jira_ticket_id || `Report #${bug.id}`}
                          {bug.jira_ticket_id && !hasRealTargetTicket && (
                            <span className="ml-1.5 text-[9px] font-normal text-amber-400">
                              (unraised in {targetProjKey})
                            </span>
                          )}
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
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="truncate">{bug.label || 'Front-End'}</span>
                          {(bug.evidence?.length || 0) > 0 && (
                            <span
                              title={`${bug.evidence.length} Coroner evidence file(s) collected`}
                              className="px-1.5 py-0.5 rounded bg-purple-950/70 text-purple-300 border border-purple-800/60 font-mono whitespace-nowrap"
                            >
                              📸 {bug.evidence.length}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          {!hasRealTargetTicket && bug.status !== 'resolved' && (
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
                              <span>{raisingBugId === bug.id ? 'Raising...' : `Raise to ${targetProjKey} Backlog`}</span>
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
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}

      <AutoTestModal
        isOpen={isAutoTestOpen}
        onClose={() => setIsAutoTestOpen(false)}
        projectId={projectId}
        onComplete={async () => {
          const updatedBugs = await api.getBugs(projectId);
          setBugs(updatedBugs);
        }}
      />

      <ProjectForm
        isOpen={isProjectConfigOpen}
        onClose={() => setIsProjectConfigOpen(false)}
        initialData={selectedProject}
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
