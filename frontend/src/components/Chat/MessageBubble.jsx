import {
  Headset,
  Search,
  Camera,
  Scale,
  Tag,
  Eye,
  FlaskConical,
  User,
  ExternalLink,
  AlertTriangle,
} from 'lucide-react';
import api from '../../services/api';
import MarkdownContent from '../Common/MarkdownContent';

const agentConfig = {
  desk_officer: { icon: Headset, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: 'Desk Officer' },
  'desk officer': { icon: Headset, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: 'Desk Officer' },
  detective: { icon: Search, color: 'text-purple-400', bg: 'bg-purple-500/10', border: 'border-purple-500/20', label: 'Detective' },
  coroner: { icon: Camera, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20', label: 'Coroner' },
  judge: { icon: Scale, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Judge' },
  sheriff: { icon: Tag, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Sheriff' },
  watcher: { icon: Eye, color: 'text-indigo-400', bg: 'bg-indigo-500/10', border: 'border-indigo-500/20', label: 'Watcher' },
  sanity: { icon: FlaskConical, color: 'text-teal-400', bg: 'bg-teal-500/10', border: 'border-teal-500/20', label: 'Sanity' },
};

export default function MessageBubble({ message }) {
  const { role, sender, agent, content, timestamp, isStatus, evidence, llmDegraded } = message;
  const isUser = role === 'user' || sender === 'user';
  const agentKey = (agent || '').toLowerCase();
  const config =
    agentConfig[agentKey] ||
    (agent
      ? { icon: User, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: agent }
      : null);
  const AgentIcon = config?.icon || User;

  if (isStatus && config) {
    return (
      <div className="flex items-center gap-2 py-1.5 px-3 animate-slide-in">
        <div className={`w-1.5 h-1.5 rounded-full ${config.color} ripple-dot`} />
        <span className={`text-xs font-medium ${config.color}`}>{config.label}</span>
        <span className="text-xs text-slate-500">{content}</span>
      </div>
    );
  }

  if (isUser) {
    return (
      <div className="flex justify-end mb-3 animate-slide-in">
        <div className="max-w-[70%]">
          <div className="bg-brand-500/20 border border-brand-500/30 rounded-2xl rounded-br-md px-4 py-3 text-sm text-slate-100">
            <MarkdownContent text={content} />
          </div>
          <div className="text-[10px] text-slate-500 mt-1 text-right">
            {timestamp ? new Date(timestamp).toLocaleTimeString() : ''}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 mb-3 animate-slide-in">
      <div
        className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-1 ${
          config?.bg || 'bg-slate-700'
        } ${config?.border || 'border-slate-600'} border`}
      >
        <AgentIcon className={`w-4 h-4 ${config?.color || 'text-slate-400'}`} />
      </div>
      <div className="max-w-[78%] min-w-0">
        {config && (
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-xs font-semibold ${config.color}`}>
              {config.label} Agent
            </span>
            {llmDegraded && (
              <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-950/70 text-amber-300 border border-amber-700/50">
                <AlertTriangle className="w-2.5 h-2.5" />
                Fallback Mode
              </span>
            )}
          </div>
        )}
        <div className="bg-slate-700/40 border border-slate-600/30 rounded-2xl rounded-tl-md px-4 py-3 text-sm text-slate-200">
          <MarkdownContent text={content} />

          {evidence && evidence.length > 0 && (
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {evidence.map((ev, idx) => {
                const evUrl = api.resolveEvidenceUrl(ev);
                const label = ev.file_name || ev.filename || `Evidence #${ev.id || idx + 1}`;
                const isVideo =
                  ev.type === 'video' || label.endsWith('.webm') || label.endsWith('.mp4');

                return (
                  <div
                    key={ev.id || `${label}-${idx}`}
                    className="rounded-lg bg-slate-900/80 border border-slate-700/60 overflow-hidden"
                  >
                    {evUrl && !isVideo && (
                      <a href={evUrl} target="_blank" rel="noopener noreferrer" className="block">
                        <img
                          src={evUrl}
                          alt={ev.description || label}
                          className="w-full h-32 object-cover bg-slate-950 hover:opacity-90 transition-opacity"
                        />
                      </a>
                    )}
                    {evUrl && isVideo && (
                      <video
                        controls
                        preload="metadata"
                        src={evUrl}
                        className="w-full h-32 bg-black"
                      />
                    )}
                    <a
                      href={evUrl || '#'}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center justify-between gap-2 px-2.5 py-1.5 hover:bg-slate-800/70 transition-colors group"
                    >
                      <span className="text-[11px] text-slate-300 group-hover:text-brand-300 truncate">
                        {label}
                      </span>
                      <ExternalLink className="w-3 h-3 text-slate-500 group-hover:text-brand-400 flex-shrink-0" />
                    </a>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <div className="text-[10px] text-slate-500 mt-1">
          {timestamp ? new Date(timestamp).toLocaleTimeString() : ''}
        </div>
      </div>
    </div>
  );
}
