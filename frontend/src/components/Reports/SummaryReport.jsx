import React, { useState } from 'react';
import { PieChart, CheckCircle2, AlertTriangle, Clock, Layers, Printer, RefreshCw, ExternalLink } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../services/api';
import BugDetailModal from '../Chat/BugDetailModal';

export default function SummaryReport({ data, onRefresh }) {
  const [selectedBug, setSelectedBug] = useState(null);

  if (!data) return null;

  const handleRaise = async (bugId) => {
    try {
      await api.raiseBugToJira(bugId);
      toast.success(`Bug #${bugId} raised to Jira Backlog`);
      if (onRefresh) await onRefresh();
    } catch (err) {
      toast.error(err.message || 'Failed to raise to Jira');
    }
  };

  const handleRetest = async (bugId) => {
    try {
      await api.retestBug(bugId);
      toast.success(`Retest completed for Bug #${bugId}`);
      if (onRefresh) await onRefresh();
    } catch (err) {
      toast.error(err.message || 'Retest failed');
    }
  };

  const handleDelete = async (bugId) => {
    try {
      await api.deleteBug(bugId);
      toast.success(`Deleted Bug #${bugId}`);
      if (onRefresh) await onRefresh();
    } catch (err) {
      toast.error(err.message || 'Failed to delete bug');
    }
  };

  return (
    <div className="space-y-6">
      {/* Header Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-slate-900/60 border border-slate-800 rounded-2xl p-5">
        <div>
          <h2 className="text-xl font-bold text-slate-100">{data.project_name} — QA Executive Summary</h2>
          <p className="text-xs text-slate-400 mt-1">
            Automated test verification, root cause tracking, and Jira defect lifecycle status.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          {onRefresh && (
            <button
              onClick={onRefresh}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Refresh</span>
            </button>
          )}
          <button
            onClick={() => window.print()}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition-colors shadow-lg shadow-cyan-900/30"
          >
            <Printer className="w-3.5 h-3.5" />
            <span>Print Report</span>
          </button>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        {/* Coverage */}
        <div className="p-4 rounded-2xl bg-slate-900/80 border border-slate-800 shadow">
          <div className="flex items-center justify-between text-cyan-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Coverage</span>
            <PieChart className="w-4 h-4" />
          </div>
          <div className="text-2xl font-bold text-slate-100">{data.coverage_percentage}%</div>
          <div className="text-[11px] text-slate-500 mt-1">
            {data.covered_modules} of {data.total_modules} modules covered
          </div>
          <div className="w-full bg-slate-800 h-1.5 rounded-full mt-3 overflow-hidden">
            <div
              className="bg-cyan-500 h-full rounded-full transition-all duration-500"
              style={{ width: `${Math.min(100, data.coverage_percentage)}%` }}
            />
          </div>
        </div>

        {/* Tickets Added */}
        <div className="p-4 rounded-2xl bg-slate-900/80 border border-slate-800 shadow">
          <div className="flex items-center justify-between text-blue-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Tickets Added</span>
            <Layers className="w-4 h-4" />
          </div>
          <div className="text-2xl font-bold text-slate-100">{data.tickets_added}</div>
          <div className="text-[11px] text-slate-500 mt-1">Total defects identified</div>
        </div>

        {/* Resolved */}
        <div className="p-4 rounded-2xl bg-slate-900/80 border border-slate-800 shadow">
          <div className="flex items-center justify-between text-emerald-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Resolved</span>
            <CheckCircle2 className="w-4 h-4" />
          </div>
          <div className="text-2xl font-bold text-emerald-400">{data.tickets_resolved}</div>
          <div className="text-[11px] text-slate-500 mt-1">Ready to Deploy verified</div>
        </div>

        {/* Failed QA */}
        <div className="p-4 rounded-2xl bg-slate-900/80 border border-slate-800 shadow">
          <div className="flex items-center justify-between text-red-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Failed QA</span>
            <AlertTriangle className="w-4 h-4" />
          </div>
          <div className="text-2xl font-bold text-red-400">{data.tickets_failed}</div>
          <div className="text-[11px] text-slate-500 mt-1">Rejected during retest</div>
        </div>

        {/* In QA */}
        <div className="p-4 rounded-2xl bg-slate-900/80 border border-slate-800 shadow">
          <div className="flex items-center justify-between text-amber-400 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">In QA</span>
            <Clock className="w-4 h-4" />
          </div>
          <div className="text-2xl font-bold text-amber-400">{data.tickets_in_qa}</div>
          <div className="text-[11px] text-slate-500 mt-1">Under Watcher monitoring</div>
        </div>
      </div>

      {/* Module Breakdown Table */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-hidden shadow">
        <div className="p-4 border-b border-slate-800 bg-slate-950/60">
          <h3 className="text-sm font-semibold text-slate-100 uppercase tracking-wider">Module Coverage & Health</h3>
        </div>
        <table className="w-full text-left text-xs">
          <thead className="bg-slate-950/40 text-slate-400 border-b border-slate-800 text-[11px] uppercase">
            <tr>
              <th className="py-3 px-4">Module / Feature</th>
              <th className="py-3 px-4 w-44">Health Distribution</th>
              <th className="py-3 px-4">Tickets Logged</th>
              <th className="py-3 px-4">Resolved</th>
              <th className="py-3 px-4">Failed QA</th>
              <th className="py-3 px-4">Open</th>
              <th className="py-3 px-4 text-right">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 text-slate-300">
            {data.module_breakdown.map((m, idx) => {
              const total = m.total_tickets || 0;
              const resolvedPct = total ? (m.resolved / total) * 100 : 0;
              const failedPct = total ? (m.failed / total) * 100 : 0;
              const openPct = total ? (m.open / total) * 100 : 0;
              return (
                <tr key={idx} className="hover:bg-slate-800/30">
                  <td className="py-3 px-4 font-semibold text-slate-100">{m.module}</td>
                  <td className="py-3 px-4">
                    <div className="w-full h-2 rounded-full bg-slate-800 overflow-hidden flex">
                      {total === 0 ? (
                        <div className="w-full bg-slate-700/40" />
                      ) : (
                        <>
                          {resolvedPct > 0 && <div className="bg-emerald-500 h-full" style={{ width: `${resolvedPct}%` }} />}
                          {failedPct > 0 && <div className="bg-red-500 h-full" style={{ width: `${failedPct}%` }} />}
                          {openPct > 0 && <div className="bg-cyan-500 h-full" style={{ width: `${openPct}%` }} />}
                        </>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-4">{m.total_tickets}</td>
                  <td className="py-3 px-4 text-emerald-400">{m.resolved}</td>
                  <td className="py-3 px-4 text-red-400">{m.failed}</td>
                  <td className="py-3 px-4 text-slate-400">{m.open}</td>
                  <td className="py-3 px-4 text-right">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                        m.status === 'Healthy'
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : m.status === 'Attention'
                          ? 'bg-red-950 text-red-400 border border-red-800'
                          : 'bg-cyan-950 text-cyan-400 border border-cyan-800'
                      }`}
                    >
                      {m.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Ticket Log details */}
      {data.all_tickets && data.all_tickets.length > 0 && (
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-hidden shadow">
          <div className="p-4 border-b border-slate-800 bg-slate-950/60 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-100 uppercase tracking-wider">Tracked & Jira Defects</h3>
            <span className="text-xs text-slate-400">{data.all_tickets.length} total ticket(s) — click any row to inspect</span>
          </div>
          <div className="divide-y divide-slate-800/60">
            {data.all_tickets.map((t) => (
              <div
                key={t.id}
                onClick={() => setSelectedBug(t)}
                className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 hover:bg-slate-800/40 cursor-pointer transition-colors"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    {t.jira_ticket_id ? (
                      <a
                        href={t.jira_ticket_url || '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-xs font-mono font-bold text-cyan-400 hover:underline flex items-center gap-1"
                      >
                        {t.jira_ticket_id}
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    ) : (
                      <span className="text-xs font-mono text-slate-500">#{t.id}</span>
                    )}
                    <span className="text-xs font-semibold text-slate-200 truncate">{t.title}</span>
                  </div>
                  <p className="text-xs text-slate-400 line-clamp-1">{t.root_cause || t.description}</p>
                </div>

                <div className="flex items-center gap-2 self-start md:self-auto flex-shrink-0">
                  <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                    {t.severity}
                  </span>
                  <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-slate-800 text-cyan-300">
                    {t.label || 'Front-End'}
                  </span>
                  <span
                    className={`text-[10px] uppercase font-semibold px-2 py-0.5 rounded ${
                      t.status === 'resolved'
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : t.status === 'failed'
                        ? 'bg-red-950 text-red-400 border border-red-800'
                        : t.status === 'in_qa'
                        ? 'bg-amber-950 text-amber-400 border border-amber-800'
                        : 'bg-blue-950 text-blue-400 border border-blue-800'
                    }`}
                  >
                    {t.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <BugDetailModal
        bug={selectedBug}
        isOpen={Boolean(selectedBug)}
        onClose={() => setSelectedBug(null)}
        onRaiseToJira={handleRaise}
        onRetest={handleRetest}
        onDelete={handleDelete}
      />
    </div>
  );
}
