import {
  Search,
  Camera,
  Scale,
  FileText,
  Tag,
  Eye,
  FlaskConical,
  User,
  ExternalLink,
} from 'lucide-react';
import api from '../../services/api';

const agentConfig = {
  detective: { icon: Search, color: 'text-purple-400', bg: 'bg-purple-500/10', border: 'border-purple-500/20', label: 'Detective' },
  coroner: { icon: Camera, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20', label: 'Coroner' },
  judge: { icon: Scale, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Judge' },
  reporter: { icon: FileText, color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/20', label: 'Reporter' },
  sheriff: { icon: Tag, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Sheriff' },
  watcher: { icon: Eye, color: 'text-indigo-400', bg: 'bg-indigo-500/10', border: 'border-indigo-500/20', label: 'Watcher' },
  sanity: { icon: FlaskConical, color: 'text-teal-400', bg: 'bg-teal-500/10', border: 'border-teal-500/20', label: 'Sanity' },
};

function formatContent(text) {
  if (!text) return null;
  const parts = [];
  const codeBlockRegex = /```(\w*)\n?([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: text.slice(lastIndex, match.index) });
    }
    parts.push({ type: 'code', language: match[1], content: match[2].trim() });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.slice(lastIndex) });
  }

  return parts.map((part, i) => {
    if (part.type === 'code') {
      return (
        <pre
          key={i}
          className="my-2 p-3 bg-slate-900/80 rounded-lg border border-slate-700/50 overflow-x-auto text-xs"
        >
          {part.language && (
            <div className="text-[10px] text-slate-500 font-mono mb-1">{part.language}</div>
          )}
          <code className="text-slate-300 font-mono">{part.content}</code>
        </pre>
      );
    }
    return (
      <div key={i} className="whitespace-pre-wrap break-words">
        {part.content.split('\n').map((line, j) => (
          <span key={j}>
            {line.startsWith('**') && line.endsWith('**') ? (
              <strong className="font-semibold">{line.slice(2, -2)}</strong>
            ) : (
              line
            )}
            {j < part.content.split('\n').length - 1 && <br />}
          </span>
        ))}
      </div>
    );
  });
}

export default function MessageBubble({ message }) {
  const { role, sender, agent, content, timestamp, isStatus, evidence } = message;
  const isUser = role === 'user' || sender === 'user';
  const agentKey = (agent || '').toLowerCase();
  const config = agentConfig[agentKey] || (agent ? { icon: User, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: agent } : null);
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
            {formatContent(content)}
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
      <div className="max-w-[75%] min-w-0">
        {config && (
          <span className={`text-xs font-semibold ${config.color} mb-1 block`}>
            {config.label} Agent
          </span>
        )}
        <div className="bg-slate-700/40 border border-slate-600/30 rounded-2xl rounded-tl-md px-4 py-3 text-sm text-slate-200">
          {formatContent(content)}
          {evidence && evidence.length > 0 && (
            <div className="mt-3 space-y-2">
              {evidence.map((ev) => (
                <a
                  key={ev.id}
                  href={api.getEvidenceUrl(ev.id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-800/60 border border-slate-600/30 hover:border-brand-500/30 transition-colors group"
                >
                  <ExternalLink className="w-3.5 h-3.5 text-slate-500 group-hover:text-brand-400" />
                  <span className="text-xs text-slate-400 group-hover:text-brand-300 truncate">
                    {ev.filename || `Evidence #${ev.id}`}
                  </span>
                </a>
              ))}
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
