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
} from 'lucide-react';
import api from '../../services/api';

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
  reporter: { icon: FileText, color: 'text-green-400', bg: 'bg-green-500/10', border: 'border-green-500/20', label: 'Reporter' },
  sheriff: { icon: Tag, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20', label: 'Sheriff' },
  watcher: { icon: Eye, color: 'text-indigo-400', bg: 'bg-indigo-500/10', border: 'border-indigo-500/20', label: 'Watcher' },
  sanity: { icon: FlaskConical, color: 'text-teal-400', bg: 'bg-teal-500/10', border: 'border-teal-500/20', label: 'Sanity' },
};

function renderInlineMarkdown(text, keyPrefix) {
  const tokenRegex = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\*[^*]+\*)/g;
  const nodes = [];
  let lastIndex = 0;
  let match;
  let idx = 0;

  while ((match = tokenRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(
        <strong key={`${keyPrefix}-b-${idx++}`} className="font-semibold text-slate-100">
          {token.slice(2, -2)}
        </strong>
      );
    } else if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(
        <code
          key={`${keyPrefix}-c-${idx++}`}
          className="px-1.5 py-0.5 rounded bg-slate-900/90 border border-slate-700/60 font-mono text-xs text-cyan-300"
        >
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith('[')) {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (linkMatch) {
        nodes.push(
          <a
            key={`${keyPrefix}-a-${idx++}`}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-cyan-400 underline hover:text-cyan-300"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        nodes.push(token);
      }
    } else if (token.startsWith('*') && token.endsWith('*')) {
      nodes.push(
        <em key={`${keyPrefix}-i-${idx++}`} className="italic text-slate-300">
          {token.slice(1, -1)}
        </em>
      );
    }
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

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

    const lines = part.content.split('\n');
    const renderedBlocks = [];
    for (let j = 0; j < lines.length; j++) {
      const line = lines[j];
      const trimmed = line.trim();
      if (!trimmed) {
        renderedBlocks.push(<div key={j} className="h-1.5" />);
        continue;
      }
      if (/^[-*_]{3,}$/.test(trimmed)) {
        renderedBlocks.push(<hr key={j} className="my-2 border-slate-700/70" />);
        continue;
      }
      if (trimmed.startsWith('#### ')) {
        renderedBlocks.push(
          <h5 key={j} className="text-xs font-bold uppercase tracking-wide text-cyan-200 pt-1">
            {renderInlineMarkdown(trimmed.slice(5), `${i}-${j}`)}
          </h5>
        );
        continue;
      }
      if (trimmed.startsWith('### ')) {
        renderedBlocks.push(
          <h4 key={j} className="text-sm font-bold text-cyan-300 pt-1.5">
            {renderInlineMarkdown(trimmed.slice(4), `${i}-${j}`)}
          </h4>
        );
        continue;
      }
      if (trimmed.startsWith('## ')) {
        renderedBlocks.push(
          <h3 key={j} className="text-sm font-bold text-white pt-1.5">
            {renderInlineMarkdown(trimmed.slice(3), `${i}-${j}`)}
          </h3>
        );
        continue;
      }
      if (trimmed.startsWith('# ')) {
        renderedBlocks.push(
          <h2 key={j} className="text-base font-bold text-white pt-1.5">
            {renderInlineMarkdown(trimmed.slice(2), `${i}-${j}`)}
          </h2>
        );
        continue;
      }
      if (trimmed.startsWith('> ')) {
        renderedBlocks.push(
          <blockquote
            key={j}
            className="border-l-2 border-cyan-500/60 pl-3 py-0.5 text-slate-300 bg-slate-900/30 rounded-r"
          >
            {renderInlineMarkdown(trimmed.slice(2), `${i}-${j}`)}
          </blockquote>
        );
        continue;
      }
      if (/^[-*•]\s+/.test(trimmed)) {
        renderedBlocks.push(
          <div key={j} className="flex items-start gap-2 pl-1">
            <span className="text-cyan-400 mt-0.5">•</span>
            <span className="flex-1">{renderInlineMarkdown(trimmed.replace(/^[-*•]\s+/, ''), `${i}-${j}`)}</span>
          </div>
        );
        continue;
      }
      const ordMatch = trimmed.match(/^(\d+[.)])\s+(.*)$/);
      if (ordMatch) {
        renderedBlocks.push(
          <div key={j} className="flex items-start gap-2 pl-1">
            <span className="text-cyan-400 font-semibold min-w-[1.25rem]">{ordMatch[1]}</span>
            <span className="flex-1">{renderInlineMarkdown(ordMatch[2], `${i}-${j}`)}</span>
          </div>
        );
        continue;
      }
      if (trimmed.startsWith('|') && trimmed.endsWith('|') && j + 1 < lines.length && lines[j + 1].trim().startsWith('|')) {
        const tableLines = [];
        const startIdx = j;
        while (j < lines.length && lines[j].trim().startsWith('|') && lines[j].trim().endsWith('|')) {
          tableLines.push(lines[j].trim());
          j++;
        }
        j -= 1;
        const rows = tableLines
          .filter((rowStr) => !/^\|[\s:|-]+\|$/.test(rowStr))
          .map((rowStr) => rowStr.slice(1, -1).split('|').map((c) => c.trim()));
        if (rows.length > 0) {
          const [headerRow, ...bodyRows] = rows;
          renderedBlocks.push(
            <div key={startIdx} className="my-2 overflow-x-auto rounded-lg border border-slate-700/60">
              <table className="w-full text-xs text-left border-collapse">
                <thead className="bg-slate-900/80 text-cyan-300">
                  <tr>
                    {headerRow.map((cell, ci) => (
                      <th key={ci} className="px-2.5 py-1.5 border-b border-slate-700/60 font-semibold">
                        {renderInlineMarkdown(cell, `${i}-${startIdx}-h-${ci}`)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/70">
                  {bodyRows.map((row, ri) => (
                    <tr key={ri} className="bg-slate-900/30">
                      {row.map((cell, ci) => (
                        <td key={ci} className="px-2.5 py-1.5 text-slate-200">
                          {renderInlineMarkdown(cell, `${i}-${startIdx}-r-${ri}-${ci}`)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
          continue;
        }
      }
      renderedBlocks.push(
        <p key={j} className="whitespace-pre-wrap">
          {renderInlineMarkdown(line, `${i}-${j}`)}
        </p>
      );
    }

    return (
      <div key={i} className="space-y-1 break-words leading-relaxed">
        {renderedBlocks}
      </div>
    );
  });
}

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
  } = message;
  const isUser = role === 'user' || sender === 'user';
  const agentKey = (agent || '').toLowerCase();
  const config =
    agentConfig[agentKey] ||
    (agent ? { icon: User, color: 'text-cyan-400', bg: 'bg-cyan-500/10', border: 'border-cyan-500/20', label: agent } : null);
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
            {content ? formatContent(content) : null}
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
            {llmMode === 'fallback' && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-950/60 border border-amber-800/50 text-amber-300">
                Local Triage
              </span>
            )}
          </div>
        )}
        <div className="bg-slate-800/70 border border-slate-700/60 rounded-2xl rounded-tl-md px-4 py-3 text-sm text-slate-200">
          {formatContent(content)}
          {isStreaming && (
            <span className="inline-block w-2 h-4 ml-1 align-middle bg-cyan-400 animate-pulse" />
          )}
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
                    {ev.file_name || ev.filename || `Evidence #${ev.id}`}
                  </span>
                </a>
              ))}
            </div>
          )}
          {llmMode === 'fallback' && llmNotice && (
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
