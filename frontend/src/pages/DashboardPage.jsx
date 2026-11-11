import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  FolderGit2,
  Bug,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  Plus,
  Bot,
  ShieldCheck,
  SlidersHorizontal,
} from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import ProjectForm from '../components/Projects/ProjectForm';

export default function DashboardPage() {
  const navigate = useNavigate();
  const projects = useAppStore((s) => s.projects);
  const setProjects = useAppStore((s) => s.setProjects);
  const watcherEvents = useAppStore((s) => s.watcherEvents);
  const latestEvent = watcherEvents[0];
  const prevEventId = useRef(latestEvent?.id);
  const reporterName = useAppStore((s) => s.reporterName);
  const setReporterName = useAppStore((s) => s.setReporterName);

  const [stats, setStats] = useState({
    totalProjects: 0,
    totalBugs: 0,
    jiraBugs: 0,
    resolvedBugs: 0,
    failedBugs: 0,
  });
  const [recentLogs, setRecentLogs] = useState([]);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(null);

  useEffect(() => {
    let cancelled = false;
    async function loadData() {
      try {
        const [projs, logs, dashStats, settingsList] = await Promise.all([
          api.getProjects(),
          api.getLogs({ limit: 10 }),
          api.getDashboardStats(),
          api.getSettings().catch(() => []),
        ]);
        if (cancelled) return;

        setProjects(projs);
        setRecentLogs(Array.isArray(logs) ? logs : []);
        setStats({
          totalProjects: dashStats.total_projects ?? projs.length,
          totalBugs: dashStats.total_bugs ?? 0,
          jiraBugs: dashStats.jira_bugs ?? 0,
          resolvedBugs: dashStats.resolved_bugs ?? 0,
          failedBugs: dashStats.failed_bugs ?? 0,
        });

        if (Array.isArray(settingsList)) {
          const rep = settingsList.find((item) => item.key === 'default_reporter');
          if (rep?.value) setReporterName(rep.value);
        }
      } catch (err) {
        if (!cancelled) {
          toast.error(err.message || 'Failed to load dashboard overview');
        }
      }
    }
    loadData();
    return () => {
      cancelled = true;
    };
  }, [setProjects, setReporterName]);

  useEffect(() => {
    if (latestEvent && latestEvent.id && latestEvent.id !== prevEventId.current) {
      prevEventId.current = latestEvent.id;
      setRecentLogs((prev) => {
        const deduped = prev.filter((item) => item.id !== latestEvent.id);
        return [latestEvent, ...deduped].slice(0, 10);
      });
    }
  }, [latestEvent]);

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      {/* Welcome Hero Banner */}
      <div className="relative overflow-hidden bg-gradient-to-r from-slate-900 via-slate-900 to-cyan-950/60 border border-slate-800 rounded-3xl p-8 shadow-xl">
        <div className="relative z-10 max-w-2xl space-y-3">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-950 border border-cyan-800 text-cyan-400 text-xs font-semibold">
            <ShieldCheck className="w-4 h-4" />
            <span>Autonomous QA Assistant Operational</span>
          </div>
          <h1 className="text-3xl font-extrabold text-slate-100 tracking-tight">
            Welcome, <span className="text-cyan-400">{reporterName}</span>
          </h1>
          <p className="text-sm text-slate-400 leading-relaxed">
            Multi-Agent QA pipeline is active: Desk Officer triages bugs in chat and dispatches agents on demand; Detective investigates root cause and drafts formatted reports matching your template by default; Coroner (live proof), Judge (verification), and Sheriff (Jira Backlog) run on request during bug reporting; and Watcher monitors Jira &quot;In QA&quot; tickets with Coroner, Detective, Judge, and Sanity Testers (always) to send clear Pass/Fail + Evidence alerts without altering Jira status.
          </p>

          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={() => {
                setEditingProject(null);
                setIsFormOpen(true);
              }}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold shadow-lg shadow-cyan-900/30 transition-all"
            >
              <Plus className="w-4 h-4" />
              <span>Create New Project</span>
            </button>
            {projects[0] && (
              <button
                onClick={() => navigate(`/chat/${projects[0].id}`)}
                className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition-all border border-slate-700"
              >
                <Bot className="w-4 h-4 text-cyan-400" />
                <span>Open QA Chat ({projects[0].name})</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow">
          <div className="flex items-center justify-between text-cyan-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Active Projects</span>
            <FolderGit2 className="w-4 h-4" />
          </div>
          <div className="text-3xl font-bold text-slate-100">{stats.totalProjects}</div>
          <p className="text-[11px] text-slate-500 mt-1">Configured QA targets</p>
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow">
          <div className="flex items-center justify-between text-blue-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Total Defects</span>
            <Bug className="w-4 h-4" />
          </div>
          <div className="text-3xl font-bold text-slate-100">{stats.totalBugs}</div>
          <p className="text-[11px] text-slate-500 mt-1">
            {stats.jiraBugs} raised to Jira Backlog
          </p>
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow">
          <div className="flex items-center justify-between text-emerald-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Resolved Defects</span>
            <CheckCircle2 className="w-4 h-4" />
          </div>
          <div className="text-3xl font-bold text-emerald-400">{stats.resolvedBugs}</div>
          <p className="text-[11px] text-slate-500 mt-1">Ready to Deploy verified</p>
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-5 shadow">
          <div className="flex items-center justify-between text-red-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Failed in QA</span>
            <AlertTriangle className="w-4 h-4" />
          </div>
          <div className="text-3xl font-bold text-red-400">{stats.failedBugs}</div>
          <p className="text-[11px] text-slate-500 mt-1">Rejected during retest</p>
        </div>
      </div>

      {/* Grid: Projects list & Recent activity feed */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Projects overview */}
        <div className="lg:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-100">Configured Projects</h2>
            <button
              onClick={() => navigate('/projects')}
              className="text-xs font-medium text-cyan-400 hover:text-cyan-300 flex items-center gap-1"
            >
              <span>View All</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="space-y-3">
            {projects.map((p) => (
              <div
                key={p.id}
                onClick={() => navigate(`/chat/${p.id}`)}
                className="flex items-center justify-between p-4 rounded-2xl bg-slate-900/70 hover:bg-slate-900 border border-slate-800 hover:border-slate-700 cursor-pointer transition-all shadow-sm"
              >
                <div className="flex items-center gap-3">
                  <div className="p-2.5 rounded-xl bg-cyan-950 text-cyan-400 border border-cyan-800">
                    <FolderGit2 className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="text-sm font-semibold text-slate-200">{p.name}</h4>
                    <span className="text-[11px] text-slate-500 font-mono">
                      Jira Key: {p.jira_project_key || 'QA'}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setEditingProject(p);
                      setIsFormOpen(true);
                    }}
                    title="Project Configuration & Report Format"
                    className="px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium inline-flex items-center gap-1"
                  >
                    <SlidersHorizontal className="w-3.5 h-3.5 text-emerald-400" />
                    <span className="hidden sm:inline">Config</span>
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/summary/${p.id}`);
                    }}
                    className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium"
                  >
                    Summary
                  </button>
                  <button
                    onClick={() => navigate(`/chat/${p.id}`)}
                    className="px-3 py-1.5 rounded-lg bg-cyan-600/10 hover:bg-cyan-600 text-cyan-400 hover:text-white text-xs font-semibold transition-all"
                  >
                    QA Chat
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Live Watcher & Agent activity feed */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-100">Live Watcher Stream</h2>
            <button
              onClick={() => navigate('/logs')}
              className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
            >
              Full Logs
            </button>
          </div>

          <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-4 divide-y divide-slate-800/60 max-h-96 overflow-y-auto">
            {recentLogs.length === 0 ? (
              <div className="text-center py-8 text-xs text-slate-500">No Watcher or agent actions recorded yet.</div>
            ) : (
              recentLogs.map((l) => (
                <div key={l.id} className="py-2.5 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between text-[11px] mb-1">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span
                        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                          l.status === 'failed'
                            ? 'bg-red-400'
                            : l.status === 'degraded' || l.level === 'change'
                            ? 'bg-amber-400'
                            : 'bg-cyan-400'
                        }`}
                      />
                      <span className="font-semibold text-slate-200 truncate">{l.agent_name}</span>
                      {l.module && (
                        <span className="px-1.5 py-0.5 rounded bg-slate-950 border border-slate-800 text-[10px] text-slate-400 truncate">
                          {l.module}
                        </span>
                      )}
                    </div>
                    <span className="text-slate-500 font-mono flex-shrink-0 ml-2">
                      {new Date(l.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                  <p className="text-xs font-mono text-cyan-300 line-clamp-1">{l.action}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      <ProjectForm
        isOpen={isFormOpen}
        onClose={() => {
          setIsFormOpen(false);
          setEditingProject(null);
        }}
        initialData={editingProject}
      />
    </div>
  );
}
