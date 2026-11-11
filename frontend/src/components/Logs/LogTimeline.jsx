import React from 'react';
import { Shield, Headset, Search, Camera, Scale, FileText, Tag, Eye, FlaskConical } from 'lucide-react';

const agentIcons = {
  'Desk Officer': Headset,
  Detective: Search,
  Coroner: Camera,
  Judge: Scale,
  Reporter: FileText,
  Sheriff: Tag,
  Watcher: Eye,
  'Sanity Testers': FlaskConical,
};

export default function LogTimeline({ logs }) {
  if (!logs || logs.length === 0) {
    return (
      <div className="p-8 text-center text-slate-500 border border-dashed border-slate-800 rounded-xl">
        No timeline events recorded yet.
      </div>
    );
  }

  return (
    <div className="relative pl-6 space-y-6 before:absolute before:left-3 before:top-2 before:bottom-2 before:w-0.5 before:bg-slate-800 py-4">
      {logs.map((log) => {
        const Icon = agentIcons[log.agent_name] || Shield;
        const isSuccess = log.status === 'success';
        const isFailed = log.status === 'failed';
        const isDegraded = log.status === 'degraded';
        const isChange =
          log.level === 'change' || log.category === 'system_change' || log.category === 'status_change';

        const borderClass = isFailed
          ? 'border-red-500 text-red-400'
          : isDegraded || isChange
          ? 'border-amber-500 text-amber-400'
          : isSuccess
          ? 'border-cyan-500 text-cyan-400'
          : 'border-slate-500 text-slate-400';

        return (
          <div key={log.id} className="relative flex items-start gap-4 group">
            <div
              className={`absolute -left-6 top-1.5 w-6 h-6 rounded-full flex items-center justify-center border-2 bg-slate-900 ${borderClass}`}
            >
              <Icon className="w-3.5 h-3.5" />
            </div>

            <div className="flex-1 bg-slate-900/60 border border-slate-800/80 rounded-xl p-4 shadow-sm hover:border-slate-700 transition-all">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-bold text-slate-100">{log.agent_name}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-slate-950 border border-slate-800 text-slate-300 font-medium">
                    {log.module || 'System'}
                  </span>
                  <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-cyan-300">
                    {log.action}
                  </span>
                  {log.jira_key && (
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-blue-950/70 border border-blue-800/60 text-blue-300">
                      {log.jira_key}
                    </span>
                  )}
                  {log.notified_channels && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-950/60 border border-purple-800/60 text-purple-300">
                      {log.notified_channels}
                    </span>
                  )}
                  {log.status === 'degraded' && (
                    <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-amber-950 text-amber-400 border border-amber-800">
                      degraded
                    </span>
                  )}
                </div>
                <span className="text-[11px] text-slate-500 font-mono">
                  {new Date(log.started_at).toLocaleTimeString()}
                </span>
              </div>

              {log.output_data && (
                <p className="text-xs text-slate-400 mt-2 line-clamp-3 font-mono bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/60">
                  {log.output_data}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
