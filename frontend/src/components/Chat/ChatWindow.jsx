import { useEffect, useRef } from 'react';
import useAppStore from '../../store/appStore';
import MessageBubble from './MessageBubble';
import { MessageSquare } from 'lucide-react';

export default function ChatWindow() {
  const messages = useAppStore((s) => s.messages);
  const chatLoading = useAppStore((s) => s.chatLoading);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, chatLoading]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-center p-8">
        <div className="w-20 h-20 rounded-2xl bg-brand-500/10 border border-brand-500/20 flex items-center justify-center mb-6">
          <MessageSquare className="w-10 h-10 text-brand-400" />
        </div>
        <h2 className="text-xl font-semibold text-white mb-2">Start a Conversation</h2>
        <p className="text-slate-400 max-w-md text-sm">
          Send a message to begin QA testing. Upload skills, trigger auto-tests, or ask
          the AI agents to investigate specific areas of your application.
        </p>
        <div className="flex flex-wrap gap-2 mt-6 justify-center">
          {[
            'Run a full regression test',
            'Check the login flow',
            'Test API endpoints',
            'Review latest changes',
          ].map((suggestion) => (
            <button
              key={suggestion}
              className="px-3 py-1.5 rounded-full text-xs font-medium bg-slate-700/50 border border-slate-600/50 text-slate-300 hover:text-brand-400 hover:border-brand-500/30 transition-all"
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
        <MessageBubble key={msg.id} message={msg} />
      ))}
      {chatLoading && (
        <div className="flex items-center gap-2 px-4 py-3 animate-fade-in">
          <div className="flex gap-1">
            <span className="w-2 h-2 bg-brand-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
            <span className="w-2 h-2 bg-brand-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
            <span className="w-2 h-2 bg-brand-400 rounded-full animate-bounce" />
          </div>
          <span className="text-sm text-slate-400">Agents are processing...</span>
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
