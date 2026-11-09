import React, { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle, Clock, AlertCircle } from 'lucide-react';

export default function LogsTable({ logs, loading }) {
  const [expandedId, setExpandedId] = useState(null);

  const toggleExpand = (id) => {
    setExpandedId(expandedId === id ? null : id);
  };

  if (loading) {
    return (
      <div className="p-12 text-center text-slate-500">
        <div className="w-6 h-6 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin mx-auto mb-2" />
        Loading activity logs...
      </div>
    );
  }

  if (!logs || logs.length === 0) {
    return (
      <div className="p-12 text-center text-slate-500 border border-dashed border-slate-800 rounded-xl">
        No agent logs matching the selected filters.
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60 shadow">
      <table className="w-full text-left text-xs">
        <thead className="bg-slate-950/80 text-slate-400 font-semibold border-b border-slate-800 uppercase tracking-wider text-[11px]">
          <tr>
            <th className="py-3 px-4 w-10"></th>
            <th className="py-3 px-4">Timestamp</th>
            <th className="py-3 px-4">Agent</th>
            <th className="py-3 px-4">Action</th>
            <th className="py-3 px-4">Bug Ref</th>
            <th className="py-3 px-4 text-right">Status</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/60 text-slate-300">
          {logs.map((log) => {
            const isExpanded = expandedId === log.id;
            const isSuccess = log.status === 'success';
            const isFailed = log.status === 'failed';

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
                  <td className="py-3 px-4 font-mono text-[11px] text-slate-400">
                    {new Date(log.started_at).toLocaleString()}
                  </td>
                  <td className="py-3 px-4 font-semibold text-slate-100 flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-cyan-400" />
                    {log.agent_name}
                  </td>
                  <td className="py-3 px-4 font-mono text-cyan-300">
                    {log.action}
                  </td>
                  <td className="py-3 px-4 text-slate-400">
                    {log.bug_report_id ? `#${log.bug_report_id}` : '—'}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                        isSuccess
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : isFailed
                          ? 'bg-red-950 text-red-400 border border-red-800'
                          : 'bg-amber-950 text-amber-400 border border-amber-800'
                      }`}
                    >
                      {isSuccess ? <CheckCircle className="w-3 h-3" /> : isFailed ? <AlertCircle className="w-3 h-3" /> : <Clock className="w-3 h-3" />}
                      {log.status}
                    </span>
                  </td>
                </tr>

                {/* Expanded Details */}
                {isExpanded && (
                  <tr className="bg-slate-950/90 border-y border-slate-800">
                    <td colSpan={6} className="p-4 space-y-3">
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

