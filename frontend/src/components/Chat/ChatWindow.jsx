import { useEffect, useRef } from 'react';
import useAppStore from '../../store/appStore';
import MessageBubble from './MessageBubble';
import { MessageSquare } from 'lucide-react';

const EMPTY_MESSAGES = [];

export default function ChatWindow({ projectId, onSuggestionClick }) {
  const pid = String(projectId || 'default');
  const messages = useAppStore((s) => s.messagesByProject[pid] || EMPTY_MESSAGES);
  const chatLoading = useAppStore((s) => s.chatLoading);
  const bottomRef = useRef(null);
  const hasActiveStream = messages.some((m) => m.isStreaming);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, chatLoading]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-center p-8">
        <div className="w-20 h-20 rounded-2xl bg-brand-500/10 border border-brand-500/20 flex items-center justify-center mb-6">
          <MessageSquare className="w-10 h-10 text-brand-400" />
        </div>
        <h2 className="text-xl font-semibold text-white mb-2">Start a Conversation with Desk Officer</h2>
        <p className="text-slate-400 max-w-md text-sm">
          Describe a bug, drop or paste screenshots, PDFs, HAR captures &amp; logs, ask about any built-in agent, or trigger targeted QA tasks interactively.
        </p>
        <div className="flex flex-wrap gap-2 mt-6 justify-center">
          {[
            'Check my Jira In QA list',
            'Show all project assets and built-in agents',
            'Organize Detective, Coroner, and Judge for a bug',
            'Create a bug report',
          ].map((suggestion) => (
            <button
              key={suggestion}
              disabled={chatLoading}
              onClick={() => onSuggestionClick && onSuggestionClick(suggestion)}
              className="px-3 py-1.5 rounded-full text-xs font-medium bg-slate-700/50 border border-slate-600/50 text-slate-300 hover:text-brand-400 hover:border-brand-500/30 transition-all disabled:opacity-50"
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-1">
      {messages.map((msg) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          disabled={chatLoading}
          onSuggestionClick={onSuggestionClick}
        />
      ))}
      {chatLoading && !hasActiveStream && (
        <div className="flex items-center gap-2 px-4 py-3 animate-fade-in">
          <div className="flex gap-1">
            <span className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
            <span className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
            <span className="w-2 h-2 bg-cyan-400 rounded-full animate-bounce" />
          </div>
          <span className="text-sm text-slate-400">Desk Officer is thinking...</span>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
