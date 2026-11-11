import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Filter,
  Table as TableIcon,
  GitCommit,
  RefreshCw,
  Eye,
  Search,
  Layers,
  AlertTriangle,
  Bell,
  Activity,
  CheckCircle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import LogsTable from '../components/Logs/LogsTable';
import LogTimeline from '../components/Logs/LogTimeline';

const DEFAULT_MODULES = [
  'Projects',
  'Skills',
  'Settings',
  'Desk Officer Chat',
  'Chat Uploads',
  'Defect Investigation',
  'Evidence & Browser',
  'Root Cause Verification',
  'Bug Reporting',
  'Jira Integration',
  'Watcher Monitor',
  'Sanity & Regression',
  'App Health',
  'Notifications',
  'API / System',
];

export default function LogsPage() {
  const projects = useAppStore((s) => s.projects);
  const setProjects = useAppStore((s) => s.setProjects);
  const watcherEvents = useAppStore((s) => s.watcherEvents);
  const latestEventId = watcherEvents[0]?.id;
  const prevLatestId = useRef(latestEventId);

  const [logs, setLogs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [lastSweep, setLastSweep] = useState(null);
  const [viewMode, setViewMode] = useState('table'); // 'table' | 'timeline'

  // Filter state
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [selectedModule, setSelectedModule] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('');
  const [selectedStatus, setSelectedStatus] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  const fetchLogsAndSummary = useCallback(
    async ({ silent = false } = {}) => {
      if (!silent) setLoading(true);
      try {
        const [logsRes, summaryRes] = await Promise.all([
          api.getLogs({
            project_id: selectedProjectId || undefined,
            module: selectedModule || undefined,
            agent: selectedAgent || undefined,
            category: selectedCategory || undefined,
            status: selectedStatus || undefined,
            search: searchTerm.trim() || undefined,
            limit: 200,
          }),
          api.getLogsSummary({
            project_id: selectedProjectId || undefined,
          }),
        ]);
        setLogs(Array.isArray(logsRes) ? logsRes : []);
        setSummary(summaryRes || null);
      } catch (err) {
        console.error('Failed to fetch logs:', err);
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [selectedProjectId, selectedModule, selectedAgent, selectedCategory, selectedStatus, searchTerm]
  );

  useEffect(() => {
    async function loadProjects() {
      try {
        const projs = await api.getProjects();
        setProjects(projs);
      } catch (err) {
        console.error(err);
      }
    }
    loadProjects();
  }, [setProjects]);

  useEffect(() => {
    fetchLogsAndSummary();
  }, [fetchLogsAndSummary]);

  // Auto-refresh quietly when Watcher broadcasts a new live telemetry event
  useEffect(() => {
    if (latestEventId && latestEventId !== prevLatestId.current) {
      prevLatestId.current = latestEventId;
      fetchLogsAndSummary({ silent: true });
    }
  }, [latestEventId, fetchLogsAndSummary]);

  const handleRunWatcherScan = async () => {
    setScanning(true);
    try {
      const sweep = await api.triggerWatcherScan({
        project_id: selectedProjectId ? Number(selectedProjectId) : null,
        include_app_health: true,
        notify: true,
      });
      setLastSweep(sweep);
      toast.success(
        `Watcher sweep complete: ${sweep.modules_monitored} module(s), ${sweep.agents_monitored} agent(s), ${sweep.logs_audited} log(s) audited.`
      );
      await fetchLogsAndSummary({ silent: true });
    } catch (err) {
      toast.error(`Watcher scan failed: ${err.message}`);
    } finally {
      setScanning(false);
    }
  };

  const moduleOptions = Array.from(
    new Set([
      ...DEFAULT_MODULES,
      ...((summary?.by_module || []).map((m) => m.module).filter(Boolean)),
    ])
  ).sort();

  const hasFilters =
    selectedProjectId ||
    selectedModule ||
    selectedAgent ||
    selectedCategory ||
    selectedStatus ||
    searchTerm.trim();

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      {/* Header & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-2.5">
            <Eye className="w-6 h-6 text-indigo-400" />
            <span>Watcher Telemetry &amp; Agent Logs</span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            System-wide audit trail across every module, every AI agent, system changes, failures, successes, and Slack/Email deliveries.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleRunWatcherScan}
            disabled={scanning}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-950/50 transition-all disabled:opacity-50"
          >
            <Eye className={`w-3.5 h-3.5 ${scanning ? 'animate-pulse' : ''}`} />
            <span>{scanning ? 'Scanning Everything...' : 'Run Watcher Scan'}</span>
          </button>

          <div className="flex bg-slate-900 border border-slate-800 rounded-xl p-1">
            <button
              onClick={() => setViewMode('table')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                viewMode === 'table' ? 'bg-cyan-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <TableIcon className="w-3.5 h-3.5" />
              <span>Table</span>
            </button>
            <button
              onClick={() => setViewMode('timeline')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                viewMode === 'timeline' ? 'bg-cyan-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <GitCommit className="w-3.5 h-3.5" />
              <span>Timeline</span>
            </button>
          </div>

          <button
            onClick={() => fetchLogsAndSummary()}
            className="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
            title="Refresh Logs"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Last Sweep Feedback Banner */}
      {lastSweep && (
        <div className="p-3.5 rounded-2xl bg-indigo-950/50 border border-indigo-800/70 flex flex-wrap items-center justify-between gap-3 text-xs text-indigo-200">
          <div className="flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            <span>
              <strong>Watcher Sweep Complete:</strong> Audited{' '}
              <strong>{lastSweep.modules_monitored}</strong> module(s),{' '}
              <strong>{lastSweep.agents_monitored}</strong> AI agent(s), and{' '}
              <strong>{lastSweep.logs_audited}</strong> log(s) ({lastSweep.failures_count} failure(s),{' '}
              {lastSweep.changes_count} change(s)). Channels:{' '}
              <code className="text-cyan-300">{lastSweep.notified_channels || 'ws'}</code>
            </span>
          </div>
          <button
            onClick={() => setLastSweep(null)}
            className="text-[11px] text-indigo-300 hover:text-white underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Telemetry KPI Strip */}
      {summary && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
            <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4">
              <div className="flex items-center justify-between text-[11px] font-semibold uppercase text-slate-400">
                <span>Audited Logs</span>
                <Activity className="w-3.5 h-3.5 text-cyan-400" />
              </div>
              <div className="text-2xl font-bold text-slate-100 mt-1">{summary.total_logs}</div>
              <div className="text-[11px] text-emerald-400 mt-0.5">
                {summary.success_count} success
              </div>
            </div>

            <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4">
              <div className="flex items-center justify-between text-[11px] font-semibold uppercase text-slate-400">
                <span>Modules Seen</span>
                <Layers className="w-3.5 h-3.5 text-indigo-400" />
              </div>
              <div className="text-2xl font-bold text-slate-100 mt-1">
                {summary.by_module?.length || 0}
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                {summary.by_agent?.length || 0} AI agent(s) active
              </div>
            </div>

            <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4">
              <div className="flex items-center justify-between text-[11px] font-semibold uppercase text-slate-400">
                <span>Changes</span>
                <RefreshCw className="w-3.5 h-3.5 text-amber-400" />
              </div>
              <div className="text-2xl font-bold text-amber-300 mt-1">{summary.change_count}</div>
              <div className="text-[11px] text-slate-500 mt-0.5">CRUD, Jira &amp; statuses</div>
            </div>

            <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4">
              <div className="flex items-center justify-between text-[11px] font-semibold uppercase text-slate-400">
                <span>Failures</span>
                <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
              </div>
              <div className="text-2xl font-bold text-red-400 mt-1">{summary.failed_count}</div>
              <div className="text-[11px] text-slate-500 mt-0.5">Errors &amp; rejections</div>
            </div>

            <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-4 col-span-2 sm:col-span-1">
              <div className="flex items-center justify-between text-[11px] font-semibold uppercase text-slate-400">
                <span>Alerts Sent</span>
                <Bell className="w-3.5 h-3.5 text-purple-400" />
              </div>
              <div className="text-2xl font-bold text-purple-300 mt-1">
                {summary.notifications?.total_external || 0}
              </div>
              <div className="text-[11px] text-slate-400 mt-0.5">
                Slack: {summary.notifications?.slack_sent || 0} · Email:{' '}
                {summary.notifications?.email_sent || 0}
              </div>
            </div>
          </div>

          {/* Quick Module Filter Pills */}
          {summary.by_module?.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-slate-500 font-semibold mr-1">Active Modules:</span>
              {summary.by_module.slice(0, 8).map((m) => (
                <button
                  key={m.module}
                  onClick={() =>
                    setSelectedModule((prev) => (prev === m.module ? '' : m.module))
                  }
                  className={`px-2.5 py-1 rounded-full text-[11px] border transition-colors ${
                    selectedModule === m.module
                      ? 'bg-cyan-950 border-cyan-600 text-cyan-300 font-semibold'
                      : 'bg-slate-900/80 border-slate-800 text-slate-300 hover:border-slate-700'
                  }`}
                >
                  {m.module} <span className="text-slate-500">({m.total})</span>
                  {m.failed > 0 && (
                    <span className="ml-1 text-red-400 font-semibold">· {m.failed} err</span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Filter Bar */}
      <div className="p-4 bg-slate-900/80 border border-slate-800 rounded-2xl flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-400">
          <Filter className="w-4 h-4 text-cyan-400" />
          <span>Filters:</span>
        </div>

        {/* Project */}
        <select
          value={selectedProjectId}
          onChange={(e) => setSelectedProjectId(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">All Projects</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>

        {/* Module */}
        <select
          value={selectedModule}
          onChange={(e) => setSelectedModule(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">All Modules</option>
          {moduleOptions.map((mod) => (
            <option key={mod} value={mod}>
              {mod}
            </option>
          ))}
        </select>

        {/* Agent */}
        <select
          value={selectedAgent}
          onChange={(e) => setSelectedAgent(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">All Agents</option>
          <option value="Desk Officer">Desk Officer</option>
          <option value="Detective">Detective</option>
          <option value="Coroner">Coroner</option>
          <option value="Judge">Judge</option>
          <option value="Reporter">Reporter</option>
          <option value="Sheriff">Sheriff</option>
          <option value="Watcher">Watcher</option>
          <option value="Sanity Testers">Sanity Testers</option>
        </select>

        {/* Category */}
        <select
          value={selectedCategory}
          onChange={(e) => setSelectedCategory(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">All Categories</option>
          <option value="agent_action">Agent Action</option>
          <option value="system_change">System Change</option>
          <option value="status_change">Status Change</option>
          <option value="jira_sync">Jira Sync</option>
          <option value="module_health">Module Health</option>
          <option value="error">Error / Failure</option>
          <option value="watcher_sweep">Watcher Sweep</option>
          <option value="notification_delivery">Notification Delivery</option>
        </select>

        {/* Status */}
        <select
          value={selectedStatus}
          onChange={(e) => setSelectedStatus(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">All Statuses</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>

        {/* Search */}
        <div className="relative flex-1 min-w-[180px]">
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search action, Jira key, module, payload..."
            className="w-full bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 pl-8 pr-3 py-1.5 focus:border-cyan-500 focus:outline-none"
          />
        </div>

        {hasFilters && (
          <button
            onClick={() => {
              setSelectedProjectId('');
              setSelectedModule('');
              setSelectedAgent('');
              setSelectedCategory('');
              setSelectedStatus('');
              setSearchTerm('');
            }}
            className="text-xs text-cyan-400 hover:underline ml-auto"
          >
            Clear Filters
          </button>
        )}
      </div>

      {/* Logs View */}
      {viewMode === 'table' ? (
        <LogsTable logs={logs} loading={loading} />
      ) : (
        <LogTimeline logs={logs} />
      )}
    </div>
  );
}
