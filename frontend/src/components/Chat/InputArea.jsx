import { useState, useRef, useCallback } from 'react';
import { Send, Paperclip, Bot, BarChart3 } from 'lucide-react';
import useAppStore from '../../store/appStore';
import api from '../../services/api';
import toast from 'react-hot-toast';
import AutoTestModal from './AutoTestModal';

const TEXT_EXTENSIONS = ['.txt', '.md', '.json', '.csv', '.log', '.xml', '.html', '.yml', '.yaml', '.js', '.jsx', '.ts', '.tsx', '.py'];

async function readAttachmentSafely(file) {
  const lower = (file.name || '').toLowerCase();
  const isText =
    (file.type && (file.type.startsWith('text/') || file.type === 'application/json' || file.type.includes('xml'))) ||
    TEXT_EXTENSIONS.some((ext) => lower.endsWith(ext));

  if (!isText) {
    const sizeKb = Math.max(1, Math.round(file.size / 1024));
    return {
      name: file.name,
      content: `[Binary attachment: ${file.name} (${sizeKb} KB, ${file.type || 'binary'}). To extract .docx requirements automatically, upload via Stored Skills.]`,
    };
  }

  const raw = await file.text();
  return {
    name: file.name,
    content: raw.slice(0, 50000),
  };
}

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
    if (sending) return;
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;

    const selectedFiles = [...files];
    setText('');
    setFiles([]);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

    setSending(true);
    try {
      const attachments = [];
      for (const file of selectedFiles) {
        attachments.push(await readAttachmentSafely(file));
      }

      if (onSendMessage) {
        await onSendMessage(trimmed, attachments);
        return;
      }

      if (!projectId) {
        toast.error('Please select a project first');
        return;
      }

      addMessage(
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          role: 'user',
          content: trimmed + (selectedFiles.length ? `\n\n📎 ${selectedFiles.map((f) => f.name).join(', ')}` : ''),
          timestamp: new Date().toISOString(),
        },
        projectId
      );

      setChatLoading(true);
      const response = await api.sendMessage({
        project_id: parseInt(projectId, 10),
        message: trimmed,
        attachments,
      });

      if (response.response) {
        addMessage(
          {
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            role: 'assistant',
            agent: response.agent || 'Desk Officer',
            content: response.response,
            evidence: response.evidence || response.pipeline_result?.evidence,
            llmDegraded: Boolean(response.llm_degraded),
            timestamp: new Date().toISOString(),
          },
          projectId
        );
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
    const newFiles = Array.from(e.target.files || []);
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
                  type="button"
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
              disabled={sending}
              onChange={(e) => {
                setText(e.target.value);
                handleResize();
              }}
              onKeyDown={handleKeyDown}
              placeholder="Describe a bug, paste logs, or tell Desk Officer what you need..."
              rows={1}
              className="w-full input-field resize-none pr-10 py-3 text-sm min-h-[44px] disabled:opacity-60"
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
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="btn-ghost p-2.5 rounded-lg"
            title="Attach files"
          >
            <Paperclip className="w-5 h-5" />
          </button>

          <button
            type="button"
            onClick={() => (onOpenAutoTest ? onOpenAutoTest() : setShowAutoTest(true))}
            className="btn-secondary flex items-center gap-2 text-sm whitespace-nowrap"
            title="Auto-Test"
          >
            <Bot className="w-4 h-4" />
            <span className="hidden md:inline">Auto-Test</span>
          </button>

          <button
            type="button"
            onClick={() => (onOpenSummary ? onOpenSummary() : handleSummary())}
            className="btn-secondary flex items-center gap-2 text-sm whitespace-nowrap"
            title="Summary Report"
          >
            <BarChart3 className="w-4 h-4" />
            <span className="hidden md:inline">Summary</span>
          </button>

          <button
            type="button"
            onClick={handleSend}
            disabled={(!text.trim() && files.length === 0) || sending}
            className="btn-primary flex items-center gap-2 px-4"
            title="Send message"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>

      {showAutoTest && (
        <AutoTestModal
          isOpen={showAutoTest}
          projectId={projectId}
          onClose={() => setShowAutoTest(false)}
        />
      )}
    </>
  );
}
