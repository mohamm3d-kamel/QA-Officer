import React, { useState, useEffect, useCallback } from 'react';
import { Filter, Table as TableIcon, GitCommit, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import LogsTable from '../components/Logs/LogsTable';
import LogTimeline from '../components/Logs/LogTimeline';

export default function LogsPage() {
  const projects = useAppStore((s) => s.projects);
  const setProjects = useAppStore((s) => s.setProjects);

  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [viewMode, setViewMode] = useState('table'); // 'table' | 'timeline'

  // Filter state
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [selectedAgent, setSelectedAgent] = useState('');
  const [selectedStatus, setSelectedStatus] = useState('');

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getLogs({
        project_id: selectedProjectId || undefined,
        agent: selectedAgent || undefined,
        status: selectedStatus || undefined,
        limit: 150,
      });
      setLogs(res);
    } catch (err) {
      toast.error(err.message || 'Failed to fetch activity logs');
    } finally {
      setLoading(false);
    }
  }, [selectedProjectId, selectedAgent, selectedStatus]);

  useEffect(() => {
    async function loadProjects() {
      try {
        const projs = await api.getProjects();
        setProjects(projs);
      } catch (err) {
        toast.error(err.message || 'Failed to load projects');
      }
    }
    loadProjects();
  }, [setProjects]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Agent Activity & Defect Logs</h1>
          <p className="text-xs text-slate-400 mt-1">
            Audit trail of root cause investigations, evidence captures, Jira transitions, and sanity tests.
          </p>
        </div>

        {/* View mode toggle & Refresh */}
        <div className="flex items-center gap-2">
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
            onClick={fetchLogs}
            className="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
            title="Refresh Logs"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="p-4 bg-slate-900/80 border border-slate-800 rounded-2xl flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-400">
          <Filter className="w-4 h-4 text-cyan-400" />
          <span>Filters:</span>
        </div>

        {/* Project select */}
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

        {/* Agent select */}
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
          <option value="Sheriff">Sheriff</option>
          <option value="Watcher">Watcher</option>
          <option value="Sanity Testers">Sanity Testers</option>
        </select>

        {/* Status select */}
        <select
          value={selectedStatus}
          onChange={(e) => setSelectedStatus(e.target.value)}
          className="bg-slate-950 border border-slate-800 rounded-lg text-xs text-slate-200 px-3 py-1.5 focus:border-cyan-500 focus:outline-none"
        >
          <option value="">All Statuses</option>
          <option value="success">Success</option>
          <option value="degraded">Degraded (Fallback)</option>
          <option value="failed">Failed</option>
          <option value="running">Running</option>
        </select>

        {(selectedProjectId || selectedAgent || selectedStatus) && (
          <button
            onClick={() => {
              setSelectedProjectId('');
              setSelectedAgent('');
              setSelectedStatus('');
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
