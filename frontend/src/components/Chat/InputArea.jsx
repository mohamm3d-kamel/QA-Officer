import { useState, useRef, useCallback } from 'react';
import { Send, Paperclip, Bot, BarChart3, Upload } from 'lucide-react';
import useAppStore from '../../store/appStore';
import api from '../../services/api';
import toast from 'react-hot-toast';
import AutoTestModal from './AutoTestModal';

export default function InputArea({ projectId, onSendMessage, onOpenAutoTest, onOpenSummary }) {
  const [text, setText] = useState('');
  const [files, setFiles] = useState([]);
  const [showAutoTest, setShowAutoTest] = useState(false);
  const [sending, setSending] = useState(false);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const addMessage = useAppStore((s) => s.addMessage);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  const handleResize = useCallback(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    }
  }, []);

  const handleSend = async () => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;

    const attachments = [];
    for (const file of files) {
      const content = await file.text();
      attachments.push({ name: file.name, content });
    }

    setText('');
    setFiles([]);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

    if (onSendMessage) {
      await onSendMessage(trimmed, attachments);
      return;
    }

    if (!projectId) {
      toast.error('Please select a project first');
      return;
    }

    addMessage({
      id: Date.now(),
      role: 'user',
      content: trimmed + (files.length ? `\n\n📎 ${files.map((f) => f.name).join(', ')}` : ''),
      timestamp: new Date().toISOString(),
    });

    setText('');
    setFiles([]);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setSending(true);
    setChatLoading(true);

    try {
      const response = await api.sendMessage({
        project_id: parseInt(projectId),
        message: trimmed,
        attachments,
      });

      if (response.response) {
        addMessage({
          id: Date.now() + 1,
          role: 'assistant',
          content: response.response,
          timestamp: new Date().toISOString(),
        });
      }

      if (response.agent_updates) {
        response.agent_updates.forEach((update, i) => {
          addMessage({
            id: Date.now() + 2 + i,
            role: 'agent',
            agent: update.agent,
            content: update.message || update.output,
            timestamp: new Date().toISOString(),
          });
        });
      }
    } catch (err) {
      toast.error(err.message || 'Failed to send message');
    } finally {
      setSending(false);
      setChatLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileChange = (e) => {
    const newFiles = Array.from(e.target.files);
    setFiles((prev) => [...prev, ...newFiles]);
    e.target.value = '';
  };

  const handleSummary = () => {
    if (projectId) {
      window.open(`/summary/${projectId}`, '_blank');
    }
  };

  return (
    <>
      <div className="border-t border-slate-700/50 bg-surface-800/80 backdrop-blur-sm p-4">
        {/* Attached files preview */}
        {files.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-3">
            {files.map((file, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-700/50 border border-slate-600/50 text-xs text-slate-300"
              >
                <Paperclip className="w-3 h-3" />
                <span className="truncate max-w-[150px]">{file.name}</span>
                <button
                  onClick={() => setFiles((f) => f.filter((_, j) => j !== i))}
                  className="text-slate-500 hover:text-red-400 ml-1"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Input row */}
        <div className="flex items-end gap-2">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                handleResize();
              }}
              onKeyDown={handleKeyDown}
              placeholder="Describe what you want to test..."
              rows={1}
              className="w-full input-field resize-none pr-10 py-3 text-sm min-h-[44px]"
            />
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFileChange}
            className="hidden"
          />

          <button
            onClick={() => fileInputRef.current?.click()}
            className="btn-ghost p-2.5 rounded-lg"
            title="Attach files"
          >
            <Paperclip className="w-5 h-5" />
          </button>

          <button
            onClick={() => onOpenAutoTest ? onOpenAutoTest() : setShowAutoTest(true)}
            className="btn-secondary flex items-center gap-2 text-sm whitespace-nowrap"
            title="Auto-Test"
          >
            <Bot className="w-4 h-4" />
            <span className="hidden md:inline">Auto-Test</span>
          </button>

          <button
            onClick={() => onOpenSummary ? onOpenSummary() : handleSummary()}
            className="btn-secondary flex items-center gap-2 text-sm whitespace-nowrap"
            title="Summary Report"
          >
            <BarChart3 className="w-4 h-4" />
            <span className="hidden md:inline">Summary</span>
          </button>

          <button
            onClick={handleSend}
            disabled={(!text.trim() && files.length === 0) || sending}
            className="btn-primary flex items-center gap-2 px-4"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>

      {showAutoTest && (
        <AutoTestModal
          projectId={projectId}
          onClose={() => setShowAutoTest(false)}
        />
      )}
    </>
  );
}
