import React, { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle, Clock, AlertCircle } from 'lucide-react';

function renderChannelBadges(notifiedChannels) {
  if (!notifiedChannels) return <span className="text-slate-600">—</span>;
  const parts = String(notifiedChannels)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return <span className="text-slate-600">—</span>;
  return (
    <div className="inline-flex flex-wrap gap-1">
      {parts.map((ch) => {
        const lower = ch.toLowerCase();
        const cls = lower.includes('failed')
          ? 'bg-red-950/80 text-red-300 border-red-800/70'
          : lower === 'slack'
          ? 'bg-purple-950/80 text-purple-300 border-purple-800/70'
          : lower === 'email'
          ? 'bg-sky-950/80 text-sky-300 border-sky-800/70'
          : 'bg-slate-800 text-slate-400 border-slate-700';
        return (
          <span
            key={ch}
            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase border ${cls}`}
          >
            {ch}
          </span>
        );
      })}
    </div>
  );
}

export default function LogsTable({ logs, loading }) {
  const [expandedId, setExpandedId] = useState(null);

  const toggleExpand = (id) => {
    setExpandedId(expandedId === id ? null : id);
  };

  if (loading) {
    return (
      <div className="p-12 text-center text-slate-500">
        <div className="w-6 h-6 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
        Loading Watcher &amp; activity logs...
      </div>
    );
  }

  if (!logs || logs.length === 0) {
    return (
      <div className="p-12 text-center text-slate-500 border border-dashed border-slate-800 rounded-xl">
        No logs matching the selected filters.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/60 shadow">
      <table className="w-full text-left text-xs">
        <thead className="bg-slate-950/80 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[11px]">
          <tr>
            <th className="py-3 px-4 w-8"></th>
            <th className="py-3 px-4">Timestamp</th>
            <th className="py-3 px-4">Module</th>
            <th className="py-3 px-4">Agent</th>
            <th className="py-3 px-4">Action</th>
            <th className="py-3 px-4">Ref</th>
            <th className="py-3 px-4">Notified</th>
            <th className="py-3 px-4 text-right">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60 text-slate-300">
          {logs.map((log) => {
            const isExpanded = expandedId === log.id;
            const isSuccess = log.status === 'success';
            const isFailed = log.status === 'failed';
            const isWarning = log.level === 'warning' || log.level === 'change';

            return (
              <React.Fragment key={log.id}>
                <tr
                  onClick={() => toggleExpand(log.id)}
                  className="hover:bg-slate-800/40 cursor-pointer transition-colors"
                >
                  <td className="py-3 px-4 text-slate-500">
                    {isExpanded ? (
                      <ChevronDown className="w-4 h-4 text-cyan-400" />
                    ) : (
                      <ChevronRight className="w-4 h-4" />
                    )}
                  </td>
                  <td className="py-3 px-4 font-mono text-[11px] text-slate-400 whitespace-nowrap">
                    {new Date(log.started_at).toLocaleString()}
                  </td>
                  <td className="py-3 px-4 whitespace-nowrap">
                    <span className="px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-[11px] text-slate-300 font-medium">
                      {log.module || 'System'}
                    </span>
                  </td>
                  <td className="py-3 px-4 font-semibold text-slate-100 whitespace-nowrap">
                    <span className="inline-flex items-center gap-2">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          isFailed
                            ? 'bg-red-400'
                            : isWarning
                            ? 'bg-amber-400'
                            : 'bg-cyan-400'
                        }`}
                      />
                      {log.agent_name}
                    </span>
                  </td>
                  <td className="py-3 px-4 font-mono text-cyan-300">
                    <div className="flex items-center gap-2">
                      <span>{log.action}</span>
                      {log.category && (
                        <span className="text-[10px] font-sans px-1.5 py-0.5 rounded bg-slate-800/80 text-slate-400">
                          {log.category}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-4 text-slate-400 whitespace-nowrap">
                    <div className="flex items-center gap-1.5">
                      {log.bug_report_id ? <span>#{log.bug_report_id}</span> : null}
                      {log.jira_key ? (
                        <span className="px-1.5 py-0.5 rounded bg-blue-950/70 border border-blue-800/60 text-[10px] font-mono text-blue-300">
                          {log.jira_key}
                        </span>
                      ) : null}
                      {!log.bug_report_id && !log.jira_key && '—'}
                    </div>
                  </td>
                  <td className="py-3 px-4 whitespace-nowrap">
                    {renderChannelBadges(log.notified_channels)}
                  </td>
                  <td className="py-3 px-4 text-right whitespace-nowrap">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                        isSuccess
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : isFailed
                          ? 'bg-red-950 text-red-400 border border-red-800'
                          : 'bg-amber-950 text-amber-400 border border-amber-800'
                      }`}
                    >
                      {isSuccess ? (
                        <CheckCircle className="w-3 h-3" />
                      ) : isFailed ? (
                        <AlertCircle className="w-3 h-3" />
                      ) : (
                        <Clock className="w-3 h-3" />
                      )}
                      {log.status}
                    </span>
                  </td>
                </tr>

                {/* Expanded Details */}
                {isExpanded && (
                  <tr className="bg-slate-950/90 border-y border-slate-800">
                    <td colSpan={8} className="p-4 space-y-3">
                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-400 pb-2 border-b border-slate-800/70">
                        <span>
                          <strong className="text-slate-300">Module:</strong> {log.module || 'System'}
                        </span>
                        <span>
                          <strong className="text-slate-300">Category:</strong> {log.category || 'agent_action'}
                        </span>
                        <span>
                          <strong className="text-slate-300">Level:</strong> {log.level || 'info'}
                        </span>
                        {log.jira_key && (
                          <span>
                            <strong className="text-slate-300">Jira:</strong> {log.jira_key}
                          </span>
                        )}
                        <span>
                          <strong className="text-slate-300">Channels:</strong>{' '}
                          {log.notified_channels || 'none'}
                        </span>
                      </div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                          <span className="text-[11px] font-semibold uppercase text-slate-400 block mb-1">
                            Input Parameters / Context
                          </span>
                          <pre className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono text-slate-300 overflow-x-auto max-h-48 whitespace-pre-wrap">
                            {log.input_data || 'None'}
                          </pre>
                        </div>
                        <div>
                          <span className="text-[11px] font-semibold uppercase text-slate-400 block mb-1">
                            Agent Output / Decision
                          </span>
                          <pre className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono text-cyan-200 overflow-x-auto max-h-48 whitespace-pre-wrap">
                            {log.output_data || 'None'}
                          </pre>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
