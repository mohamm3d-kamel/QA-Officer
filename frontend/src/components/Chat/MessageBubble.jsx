import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Headset,
  Search,
  Camera,
  Scale,
  FileText,
  Tag,
  Eye,
  FlaskConical,
  User,
  ExternalLink,
  Sparkles,
  Settings2,
  Paperclip,
  Image as ImageIcon,
  AlertTriangle,
} from 'lucide-react';
import api from '../../services/api';
import MarkdownContent from '../Common/MarkdownContent';

function formatAttachmentBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

const agentConfig = {
  desk_officer: { icon: Headset, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: 'Desk Officer' },
  'desk officer': { icon: Headset, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: 'Desk Officer' },
  detective: { icon: Search, color: 'text-purple-400', bg: 'bg-purple-500/10', border: 'border-purple-500/20', label: 'Detective' },
  coroner: { icon: Camera, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20', label: 'Coroner' },
  judge: { icon: Scale, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20', label: 'Judge' },
  sheriff: { icon: Tag, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Sheriff' },
  watcher: { icon: Eye, color: 'text-indigo-400', bg: 'bg-indigo-500/10', border: 'border-indigo-500/20', label: 'Watcher' },
  sanity: { icon: FlaskConical, color: 'text-teal-400', bg: 'bg-teal-500/10', border: 'border-teal-500/20', label: 'Sanity Testers' },
  'sanity testers': { icon: FlaskConical, color: 'text-teal-400', bg: 'bg-teal-500/10', border: 'border-teal-500/20', label: 'Sanity Testers' },
};

export default function MessageBubble({ message, onSuggestionClick, disabled }) {
  const navigate = useNavigate();
  const {
    role,
    sender,
    agent,
    content,
    timestamp,
    isStatus,
    evidence,
    attachments,
    isStreaming,
    suggested_actions: suggestedActions,
    llm_mode: llmMode,
    llm_notice: llmNotice,
    llmDegraded,
  } = message;

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
    const userAttachments = Array.isArray(attachments) ? attachments : [];
    return (
      <div className="flex justify-end mb-3 animate-slide-in">
        <div className="max-w-[75%]">
          <div className="bg-brand-500/20 border border-brand-500/30 rounded-2xl rounded-br-md px-4 py-3 text-sm text-slate-100 space-y-2.5">
            {content ? <MarkdownContent text={content} /> : null}
            {userAttachments.length > 0 && (
              <div className="space-y-2">
                {userAttachments.map((att, idx) => {
                  const key = att.id || att.stored_name || `${att.name || 'att'}-${idx}`;
                  const isImg = Boolean(att.is_image);
                  const sizeLabel = formatAttachmentBytes(att.size_bytes);
                  const dims = att.width && att.height ? `${att.width}×${att.height}` : '';
                  const metaLine = [dims, sizeLabel].filter(Boolean).join(' · ') || att.summary || '';

                  if (isImg && att.url) {
                    return (
                      <a
                        key={key}
                        href={att.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="block rounded-xl overflow-hidden border border-white/15 bg-slate-950/60 hover:border-cyan-400/60 transition-colors"
                      >
                        <img
                          src={att.url}
                          alt={att.name || 'Uploaded image'}
                          className="max-h-60 w-auto max-w-full object-contain mx-auto bg-slate-950"
                        />
                        <div className="px-2.5 py-1.5 bg-slate-900/80 border-t border-white/10 flex items-center justify-between gap-2 text-[11px] text-slate-300">
                          <span className="inline-flex items-center gap-1.5 truncate">
                            <ImageIcon className="w-3 h-3 text-cyan-400 flex-shrink-0" />
                            <span className="truncate font-medium">{att.name || 'image'}</span>
                          </span>
                          {metaLine && (
                            <span className="text-[10px] text-slate-400 whitespace-nowrap">{metaLine}</span>
                          )}
                        </div>
                      </a>
                    );
                  }

                  const cardBody = (
                    <div className="flex items-center gap-2.5 px-3 py-2 rounded-xl bg-slate-900/70 border border-white/15 hover:border-cyan-400/50 transition-colors">
                      <div className="w-8 h-8 rounded-lg bg-cyan-950/80 border border-cyan-700/50 flex items-center justify-center flex-shrink-0">
                        {att.url ? (
                          <FileText className="w-4 h-4 text-cyan-300" />
                        ) : (
                          <Paperclip className="w-4 h-4 text-slate-400" />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="text-xs font-medium text-slate-100 truncate">
                          {att.name || 'Attachment'}
                        </div>
                        <div className="text-[10px] text-slate-400 truncate">
                          {att.summary || metaLine || att.mime_type || 'File'}
                        </div>
                      </div>
                      {Array.isArray(att.highlights) && att.highlights.length > 0 && (
                        <span className="px-1.5 py-0.5 rounded bg-amber-950/80 border border-amber-700/50 text-[10px] text-amber-300 whitespace-nowrap">
                          {att.highlights.length} signal{att.highlights.length > 1 ? 's' : ''}
                        </span>
                      )}
                      {att.url && <ExternalLink className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />}
                    </div>
                  );

                  return att.url ? (
                    <a
                      key={key}
                      href={att.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block"
                    >
                      {cardBody}
                    </a>
                  ) : (
                    <div key={key}>{cardBody}</div>
                  );
                })}
              </div>
            )}
          </div>
          <div className="text-[10px] text-slate-500 mt-1 text-right">
            {timestamp ? new Date(timestamp).toLocaleTimeString() : ''}
          </div>
        </div>
      </div>
    );
  }

  const isFallbackBadge = llmMode === 'fallback' || Boolean(llmDegraded);

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
            {llmMode === 'live' && (
              <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-950/70 border border-emerald-800/60 text-emerald-400">
                <Sparkles className="w-2.5 h-2.5" />
                Live AI
              </span>
            )}
            {isFallbackBadge && (
              <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-950/70 border border-amber-700/50 text-amber-300">
                <AlertTriangle className="w-2.5 h-2.5" />
                {llmMode === 'fallback' ? 'Local Triage' : 'Fallback Mode'}
              </span>
            )}
          </div>
        )}
        <div className="bg-slate-800/70 border border-slate-700/60 rounded-2xl rounded-tl-md px-4 py-3 text-sm text-slate-200">
          <MarkdownContent text={content} />
          {isStreaming && (
            <span className="inline-block w-2 h-4 ml-1 align-middle bg-cyan-400 animate-pulse" />
          )}
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
          {isFallbackBadge && llmNotice && (
            <div className="mt-2.5 pt-2 border-t border-slate-700/60 flex items-center justify-between gap-2 text-[11px] text-amber-300/90">
              <span className="truncate">{llmNotice}</span>
              <button
                onClick={() => navigate('/settings')}
                className="flex items-center gap-1 text-cyan-400 hover:underline whitespace-nowrap font-semibold"
              >
                <Settings2 className="w-3 h-3" />
                <span>Configure AI</span>
              </button>
            </div>
          )}
        </div>

        {/* Interactive Suggested Action Chips */}
        {!isStreaming && Array.isArray(suggestedActions) && suggestedActions.length > 0 && onSuggestionClick && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {suggestedActions.map((actionText, idx) => (
              <button
                key={idx}
                disabled={disabled}
                onClick={() => onSuggestionClick(actionText)}
                className="px-2.5 py-1 rounded-full text-[11px] font-medium bg-slate-800/80 hover:bg-cyan-950/80 text-cyan-300 border border-slate-700 hover:border-cyan-600/60 transition-all disabled:opacity-50"
              >
                {actionText}
              </button>
            ))}
          </div>
        )}

        <div className="text-[10px] text-slate-500 mt-1">
          {timestamp ? new Date(timestamp).toLocaleTimeString() : ''}
        </div>
      </div>
    </div>
  );
}
