import React from 'react';
import { Search, Camera, Scale, FileText, Tag, Eye, FlaskConical, CheckCircle, Clock, AlertTriangle } from 'lucide-react';
import useAppStore from '../../store/appStore';

const AGENTS = [
  { key: 'detective', name: 'Detective', role: 'Root Cause Investigator', icon: Search, color: 'text-purple-400', border: 'border-purple-500', bg: 'bg-purple-900/30' },
  { key: 'coroner', name: 'Coroner', role: 'Evidence Capture', icon: Camera, color: 'text-amber-400', border: 'border-amber-500', bg: 'bg-amber-900/30' },
  { key: 'judge', name: 'Judge', role: 'Independent Verifier', icon: Scale, color: 'text-red-400', border: 'border-red-500', bg: 'bg-red-900/30' },
  { key: 'reporter', name: 'Reporter', role: 'Bug Report Author', icon: FileText, color: 'text-emerald-400', border: 'border-emerald-500', bg: 'bg-emerald-900/30' },
  { key: 'sheriff', name: 'Sheriff', role: 'Jira Commander', icon: Tag, color: 'text-blue-400', border: 'border-blue-500', bg: 'bg-blue-900/30' },
  { key: 'watcher', name: 'Watcher', role: 'Jira Sentinel (In QA)', icon: Eye, color: 'text-indigo-400', border: 'border-indigo-500', bg: 'bg-indigo-900/30' },
  { key: 'sanity', name: 'Sanity Testers', role: 'Regression Verifier', icon: FlaskConical, color: 'text-teal-400', border: 'border-teal-500', bg: 'bg-teal-900/30' },
];

export default function AgentStatus() {
  const agentStatuses = useAppStore((s) => s.agentStatuses);

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-lg backdrop-blur">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-slate-200 tracking-wide uppercase">AI Agent Pipeline</h3>
        <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-950 text-cyan-400 border border-cyan-800">
          Automated QA
        </span>
      </div>

      <div className="relative pl-6 space-y-4 before:absolute before:left-2.5 before:top-3 before:bottom-3 before:w-0.5 before:bg-slate-800">
        {AGENTS.map((agent) => {
          const statusObj = agentStatuses[agent.key] || { status: 'idle', message: 'Standing by' };
          const isRunning = statusObj.status === 'running';
          const isSuccess = statusObj.status === 'success';
          const isFailed = statusObj.status === 'failed';
          const Icon = agent.icon;

          return (
            <div key={agent.key} className="relative flex items-start gap-3 group">
              {/* Connector bullet */}
              <div
                className={`absolute -left-6 top-1.5 w-5 h-5 rounded-full flex items-center justify-center border-2 transition-all ${
                  isRunning
                    ? 'border-cyan-400 bg-cyan-950 animate-pulse ring-4 ring-cyan-500/20'
                    : isSuccess
                    ? 'border-emerald-400 bg-emerald-950'
                    : isFailed
                    ? 'border-red-400 bg-red-950'
                    : 'border-slate-700 bg-slate-900'
                }`}
              >
                {isSuccess ? (
                  <CheckCircle className="w-3 h-3 text-emerald-400" />
                ) : isFailed ? (
                  <AlertTriangle className="w-3 h-3 text-red-400" />
                ) : isRunning ? (
                  <div className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                ) : (
                  <Clock className="w-2.5 h-2.5 text-slate-600" />
                )}
              </div>

              {/* Agent card */}
              <div
                className={`flex-1 p-2.5 rounded-lg border transition-all ${
                  isRunning
                    ? `${agent.bg} ${agent.border} shadow-md shadow-cyan-950/50`
                    : 'bg-slate-950/40 border-slate-800/80 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Icon className={`w-4 h-4 ${agent.color}`} />
                    <span className="text-xs font-medium text-slate-200">{agent.name}</span>
                  </div>
                  <span
                    className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${
                      isRunning
                        ? 'bg-cyan-500/20 text-cyan-300 animate-pulse'
                        : isSuccess
                        ? 'bg-emerald-500/20 text-emerald-300'
                        : isFailed
                        ? 'bg-red-500/20 text-red-300'
                        : 'text-slate-500'
                    }`}
                  >
                    {statusObj.status}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 mt-1 line-clamp-2">
                  {statusObj.message || agent.role}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

