import React, { useEffect, useState } from 'react';
import {
  X,
  Copy,
  Check,
  ExternalLink,
  ArrowUpRight,
  RefreshCw,
  Trash2,
  Camera,
  Film,
  Bug,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../services/api';
import MarkdownContent from '../Common/MarkdownContent';

export default function BugDetailModal({
  bug: initialBug,
  isOpen,
  onClose,
  onRaiseToJira,
  onRetest,
  onDelete,
}) {
  const [bug, setBug] = useState(initialBug || null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [acting, setActing] = useState('');

  useEffect(() => {
    if (!isOpen || !initialBug?.id) {
      setBug(null);
      return;
    }
    setBug(initialBug);
    let cancelled = false;
    setLoading(true);
    api
      .getBug(initialBug.id)
      .then((full) => {
        if (!cancelled) setBug(full);
      })
      .catch(() => {
        // Fallback cleanly to initialBug data passed by caller
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, initialBug]);

  if (!isOpen || !bug) return null;

  const evidenceList = bug.evidence || [];

  const handleCopy = async () => {
    const content = bug.formatted_report || `${bug.title}\n\n${bug.root_cause || ''}`;
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      toast.success('Bug report copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Clipboard copy unavailable');
    }
  };

  const handleAction = async (actionName, handler) => {
    if (!handler) return;
    setActing(actionName);
    try {
      await handler(bug.id);
      if (actionName === 'delete') {
        onClose();
      } else {
        const refreshed = await api.getBug(bug.id).catch(() => null);
        if (refreshed) setBug(refreshed);
      }
    } finally {
      setActing('');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-4xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-slate-800 bg-slate-950/70">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1.5">
              <span className="inline-flex items-center gap-1 text-xs font-mono font-bold text-cyan-400">
                <Bug className="w-3.5 h-3.5" />
                Report #{bug.id}
              </span>
              {bug.jira_ticket_id && (
                <a
                  href={bug.jira_ticket_url || '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-mono font-semibold px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800 hover:underline"
                >
                  {bug.jira_ticket_id}
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
              <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                {bug.severity || 'medium'}
              </span>
              <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-slate-800 text-cyan-300">
                {bug.label || 'Front-End'}
              </span>
              <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-slate-800 text-purple-300">
                {bug.module || 'General'}
              </span>
              <span
                className={`text-[10px] uppercase font-semibold px-2 py-0.5 rounded ${
                  bug.status === 'resolved'
                    ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                    : bug.status === 'failed'
                    ? 'bg-red-950 text-red-400 border border-red-800'
                    : bug.status === 'in_qa'
                    ? 'bg-amber-950 text-amber-400 border border-amber-800'
                    : 'bg-blue-950 text-blue-400 border border-blue-800'
                }`}
              >
                {bug.status}
              </span>
            </div>
            <h2 className="text-base font-bold text-slate-100 leading-snug">{bug.title}</h2>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 flex-shrink-0"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Action Toolbar */}
        <div className="px-6 py-3 border-b border-slate-800/80 bg-slate-900/90 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium transition-colors"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? 'Copied' : 'Copy Report'}</span>
            </button>

            {!bug.jira_ticket_id && bug.status !== 'resolved' && onRaiseToJira && (
              <button
                onClick={() => handleAction('raise', onRaiseToJira)}
                disabled={Boolean(acting)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600/20 hover:bg-blue-600 text-blue-300 hover:text-white border border-blue-700/60 text-xs font-semibold transition-all disabled:opacity-50"
              >
                <ArrowUpRight className="w-3.5 h-3.5" />
                <span>{acting === 'raise' ? 'Raising...' : 'Raise to Backlog'}</span>
              </button>
            )}

            {bug.status !== 'resolved' && onRetest && (
              <button
                onClick={() => handleAction('retest', onRetest)}
                disabled={Boolean(acting)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-cyan-600/20 hover:bg-cyan-600 text-cyan-300 hover:text-white border border-cyan-700/60 text-xs font-semibold transition-all disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${acting === 'retest' ? 'animate-spin' : ''}`} />
                <span>{acting === 'retest' ? 'Retesting...' : 'Retest in QA'}</span>
              </button>
            )}
          </div>

          {onDelete && (
            <button
              onClick={() => {
                if (window.confirm(`Delete Bug Report #${bug.id}?`)) {
                  handleAction('delete', onDelete);
                }
              }}
              disabled={Boolean(acting)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-xs font-semibold transition-colors disabled:opacity-50"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>{acting === 'delete' ? 'Deleting...' : 'Delete'}</span>
            </button>
          )}
        </div>

        {/* Scrollable Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {loading && (
            <div className="text-xs text-slate-400">Refreshing full defect details...</div>
          )}

          {/* Root Cause Summary */}
          {bug.root_cause && (
            <div className="p-4 rounded-xl bg-slate-950/70 border border-slate-800 space-y-1.5">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-amber-400">
                {bug.status === 'confirmed' ? 'Judge Verified Root Cause' : 'Detective Root Cause Analysis'}
              </div>
              <p className="text-sm text-slate-200 leading-relaxed">{bug.root_cause}</p>
            </div>
          )}

          {/* Evidence Gallery (Images & Videos) */}
          {evidenceList.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Visual Evidence ({evidenceList.length})
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {evidenceList.map((ev, idx) => {
                  const evUrl = api.resolveEvidenceUrl(ev);
                  const isVideo = ev.type === 'video' || (ev.file_name || '').endsWith('.webm') || (ev.file_name || '').endsWith('.mp4');
                  return (
                    <div
                      key={ev.id || `${ev.file_name}-${idx}`}
                      className="rounded-xl bg-slate-950 border border-slate-800 overflow-hidden"
                    >
                      <div className="px-3 py-2 border-b border-slate-800 flex items-center justify-between text-xs">
                        <span className="flex items-center gap-1.5 font-medium text-slate-300 truncate">
                          {isVideo ? <Film className="w-3.5 h-3.5 text-amber-400" /> : <Camera className="w-3.5 h-3.5 text-cyan-400" />}
                          <span className="truncate">{ev.file_name || ev.filename || `Evidence #${idx + 1}`}</span>
                        </span>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">
                            {ev.stage}
                          </span>
                          {evUrl && (
                            <a
                              href={evUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-cyan-400 hover:text-cyan-300"
                              title="Open full file"
                            >
                              <ExternalLink className="w-3.5 h-3.5" />
                            </a>
                          )}
                        </div>
                      </div>
                      {evUrl && (
                        isVideo ? (
                          <video
                            controls
                            preload="metadata"
                            src={evUrl}
                            className="w-full max-h-64 bg-black"
                          />
                        ) : (
                          <a href={evUrl} target="_blank" rel="noopener noreferrer" className="block bg-slate-950">
                            <img
                              src={evUrl}
                              alt={ev.description || ev.file_name || 'Bug evidence'}
                              className="w-full max-h-64 object-contain"
                            />
                          </a>
                        )
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Formatted Report Markdown */}
          {bug.formatted_report && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Formatted Bug Report
              </h3>
              <div className="p-5 rounded-xl bg-slate-950/70 border border-slate-800 text-sm text-slate-200">
                <MarkdownContent text={bug.formatted_report} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
