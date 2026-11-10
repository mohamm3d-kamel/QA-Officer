import React from 'react';
import { Clock, Shield, Headset, Search, Camera, Scale, FileText, Tag, Eye, FlaskConical } from 'lucide-react';

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

        return (
          <div key={log.id} className="relative flex items-start gap-4 group">
            <div
              className={`absolute -left-6 top-1.5 w-6 h-6 rounded-full flex items-center justify-center border-2 bg-slate-900 ${
                isSuccess ? 'border-cyan-500 text-cyan-400' : 'border-red-500 text-red-400'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
            </div>

            <div className="flex-1 bg-slate-900/60 border border-slate-800/80 rounded-xl p-4 shadow-sm hover:border-slate-700 transition-all">
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold text-slate-100">{log.agent_name}</span>
                  <span className="text-[11px] font-mono px-2 py-0.5 rounded bg-slate-800 text-cyan-300">
                    {log.action}
                  </span>
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

