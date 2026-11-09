import React from 'react';
import { PieChart, CheckCircle2, AlertTriangle, Clock, Layers, Printer, RefreshCw, ExternalLink } from 'lucide-react';

export default function SummaryReport({ data, onRefresh }) {
  if (!data) return null;

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
              style={{ width: `${data.coverage_percentage}%` }}
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
              <th className="py-3 px-4">Tickets Logged</th>
              <th className="py-3 px-4">Resolved</th>
              <th className="py-3 px-4">Failed QA</th>
              <th className="py-3 px-4">Open</th>
              <th className="py-3 px-4 text-right">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 text-slate-300">
            {data.module_breakdown.map((m, idx) => (
              <tr key={idx} className="hover:bg-slate-800/30">
                <td className="py-3 px-4 font-semibold text-slate-100">{m.module}</td>
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
            ))}
          </tbody>
        </table>
      </div>

      {/* Ticket Log details */}
      {data.all_tickets && data.all_tickets.length > 0 && (
        <div className="bg-slate-900/80 border border-slate-800 rounded-2xl overflow-hidden shadow">
          <div className="p-4 border-b border-slate-800 bg-slate-950/60 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-100 uppercase tracking-wider">Managed Jira Defects</h3>
            <span className="text-xs text-slate-400">{data.all_tickets.length} total tickets</span>
          </div>
          <div className="divide-y divide-slate-800/60">
            {data.all_tickets.map((t) => (
              <div key={t.id} className="p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 hover:bg-slate-800/30 transition-colors">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    {t.jira_ticket_id ? (
                      <a
                        href={t.jira_ticket_url || '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs font-mono font-bold text-cyan-400 hover:underline flex items-center gap-1"
                      >
                        {t.jira_ticket_id}
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    ) : (
                      <span className="text-xs font-mono text-slate-500">#{t.id}</span>
                    )}
                    <span className="text-xs font-semibold text-slate-200">{t.title}</span>
                  </div>
                  <p className="text-xs text-slate-400 line-clamp-1">{t.root_cause || t.description}</p>
                </div>

                <div className="flex items-center gap-2 self-start md:self-auto">
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
    </div>
  );
}

