import { useState, useRef, useCallback, useEffect } from 'react';
import { Send, Paperclip, Bot, BarChart3, FileText, Image as ImageIcon, X, Loader2 } from 'lucide-react';
import useAppStore from '../../store/appStore';
import api from '../../services/api';
import toast from 'react-hot-toast';
import AutoTestModal from './AutoTestModal';

const MAX_FILE_BYTES = 20 * 1024 * 1024;
const MAX_FILES_PER_MESSAGE = 8;

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function isImageFile(file) {
  if (file?.type && file.type.startsWith('image/')) return true;
  return /\.(png|jpe?g|webp|gif|bmp|tiff?|ico)$/i.test(file?.name || '');
}

function fileExtLabel(name = '') {
  const m = String(name).match(/\.([a-z0-9]+)$/i);
  return m ? m[1].toUpperCase().slice(0, 5) : 'FILE';
}

export default function InputArea({
  projectId,
  onSendMessage,
  onOpenAutoTest,
  onOpenSummary,
  droppedFiles,
  onClearDroppedFiles,
}) {
  const [text, setText] = useState('');
  const [pendingFiles, setPendingFiles] = useState([]);
  const [showAutoTest, setShowAutoTest] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const objectUrlsRef = useRef(new Set());

  const addMessage = useAppStore((s) => s.addMessage);
  const chatLoading = useAppStore((s) => s.chatLoading);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  useEffect(() => {
    const urls = objectUrlsRef.current;
    return () => {
      urls.forEach((url) => URL.revokeObjectURL(url));
      urls.clear();
    };
  }, []);

  const addPendingFiles = useCallback((rawList) => {
    const incoming = Array.from(rawList || []);
    if (incoming.length === 0) return;

    const valid = [];
    for (const file of incoming) {
      if (file.size === 0) {
        toast.error(`File "${file.name}" is empty.`);
        continue;
      }
      if (file.size > MAX_FILE_BYTES) {
        toast.error(`"${file.name}" exceeds the 20 MB upload limit.`);
        continue;
      }
      const img = isImageFile(file);
      let previewUrl = null;
      if (img) {
        previewUrl = URL.createObjectURL(file);
        objectUrlsRef.current.add(previewUrl);
      }
      valid.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        previewUrl,
        isImage: img,
      });
    }

    if (valid.length === 0) return;

    setPendingFiles((prev) => {
      const combined = [...prev, ...valid];
      if (combined.length > MAX_FILES_PER_MESSAGE) {
        toast.error(`You can attach up to ${MAX_FILES_PER_MESSAGE} files per message.`);
        combined.slice(MAX_FILES_PER_MESSAGE).forEach((item) => {
          if (item.previewUrl) {
            URL.revokeObjectURL(item.previewUrl);
            objectUrlsRef.current.delete(item.previewUrl);
          }
        });
        return combined.slice(0, MAX_FILES_PER_MESSAGE);
      }
      return combined;
    });
  }, []);

  useEffect(() => {
    if (Array.isArray(droppedFiles) && droppedFiles.length > 0) {
      addPendingFiles(droppedFiles);
      onClearDroppedFiles?.();
    }
  }, [droppedFiles, addPendingFiles, onClearDroppedFiles]);

  const removePendingFile = (id) => {
    setPendingFiles((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target?.previewUrl) {
        URL.revokeObjectURL(target.previewUrl);
        objectUrlsRef.current.delete(target.previewUrl);
      }
      return prev.filter((p) => p.id !== id);
    });
  };

  const clearAllPending = () => {
    setPendingFiles((prev) => {
      prev.forEach((item) => {
        if (item.previewUrl) {
          URL.revokeObjectURL(item.previewUrl);
          objectUrlsRef.current.delete(item.previewUrl);
        }
      });
      return [];
    });
  };

  const handleResize = useCallback(() => {
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    }
  }, []);

  const handleSend = async () => {
    const trimmed = text.trim();
    if ((!trimmed && pendingFiles.length === 0) || uploading || sending || chatLoading) return;

    if (!onSendMessage && !projectId) {
      toast.error('Please select a project first');
      return;
    }

    let uploadedAttachments = [];
    if (pendingFiles.length > 0) {
      setUploading(true);
      try {
        const res = await api.uploadChatFiles(
          projectId,
          pendingFiles.map((item) => item.file)
        );
        uploadedAttachments = Array.isArray(res?.files) ? res.files : [];
      } catch (err) {
        toast.error(err?.message || 'Failed to upload attachment(s)');
        setUploading(false);
        return;
      } finally {
        setUploading(false);
      }
    }

    setText('');
    clearAllPending();
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

    setSending(true);
    try {
      if (onSendMessage) {
        await onSendMessage(trimmed, uploadedAttachments);
        return;
      }

      addMessage(
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          sender: 'user',
          role: 'user',
          content: trimmed,
          attachments: uploadedAttachments,
          timestamp: new Date().toISOString(),
        },
        projectId
      );

      setChatLoading(true);
      const response = await api.sendMessage({
        project_id: parseInt(projectId, 10),
        message: trimmed,
        attachments: uploadedAttachments,
      });

      if (response.response) {
        addMessage(
          {
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            sender: 'agent',
            role: 'assistant',
            agent: response.agent || 'Desk Officer',
            content: response.response,
            evidence: response.evidence || response.pipeline_result?.evidence,
            llm_mode: response.llm_mode,
            llm_notice: response.llm_notice,
            llmDegraded: Boolean(response.llm_degraded),
            timestamp: new Date().toISOString(),
          },
          projectId
        );
      }
    } catch (err) {
      toast.error(err?.message || 'Failed to send message');
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

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const pasted = [];
    for (const item of items) {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) {
          const hasExt = /\.[a-z0-9]+$/i.test(file.name || '');
          if (!hasExt || file.name === 'image.png') {
            const ext = (file.type && file.type.split('/')[1]?.split('+')[0]) || 'png';
            pasted.push(new File([file], `pasted-${Date.now()}.${ext}`, { type: file.type || 'image/png' }));
          } else {
            pasted.push(file);
          }
        }
      }
    }
    if (pasted.length > 0) {
      e.preventDefault();
      addPendingFiles(pasted);
      toast.success(`Attached ${pasted.length} pasted file(s)`);
    }
  };

  const handleFileChange = (e) => {
    addPendingFiles(e.target.files);
    e.target.value = '';
  };

  const handleSummary = () => {
    if (projectId) {
      window.open(`/summary/${projectId}`, '_blank');
    }
  };

  const isBusy = uploading || sending || chatLoading;

  return (
    <>
      <div
        className={`border-t border-slate-700/50 bg-surface-800/80 backdrop-blur-sm p-4 transition-colors ${
          dragActive ? 'ring-2 ring-inset ring-cyan-500/70 bg-cyan-950/20' : ''
        }`}
        onDragOver={(e) => {
          if (e.dataTransfer?.types?.includes('Files')) {
            e.preventDefault();
            setDragActive(true);
          }
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget)) {
            setDragActive(false);
          }
        }}
        onDrop={(e) => {
          if (e.dataTransfer?.files?.length) {
            e.preventDefault();
            e.stopPropagation();
            setDragActive(false);
            addPendingFiles(e.dataTransfer.files);
          }
        }}
      >
        {/* Pending attached images & files preview */}
        {pendingFiles.length > 0 && (
          <div className="flex flex-wrap gap-2.5 mb-3">
            {pendingFiles.map((item) => (
              <div
                key={item.id}
                className="relative group flex items-center gap-2.5 px-2.5 py-2 rounded-xl bg-slate-800/90 border border-slate-600/60 text-xs text-slate-200 max-w-[240px]"
              >
                {item.isImage && item.previewUrl ? (
                  <img
                    src={item.previewUrl}
                    alt={item.file.name}
                    className="w-10 h-10 rounded-lg object-cover border border-slate-600 flex-shrink-0"
                  />
                ) : (
                  <div className="w-10 h-10 rounded-lg bg-cyan-950/80 border border-cyan-700/50 flex flex-col items-center justify-center flex-shrink-0 text-cyan-300">
                    {item.isImage ? (
                      <ImageIcon className="w-4 h-4" />
                    ) : (
                      <>
                        <FileText className="w-3.5 h-3.5" />
                        <span className="text-[8px] font-mono font-bold leading-none mt-0.5">
                          {fileExtLabel(item.file.name)}
                        </span>
                      </>
                    )}
                  </div>
                )}
                <div className="min-w-0 flex-1 pr-3">
                  <div className="truncate font-medium text-slate-200">{item.file.name}</div>
                  <div className="text-[10px] text-slate-400">{formatBytes(item.file.size)}</div>
                </div>
                <button
                  type="button"
                  disabled={uploading}
                  onClick={() => removePendingFile(item.id)}
                  aria-label={`Remove ${item.file.name}`}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-slate-900 border border-slate-600 text-slate-400 hover:text-red-400 hover:border-red-500/60 flex items-center justify-center transition-colors disabled:opacity-50"
                >
                  <X className="w-3 h-3" />
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
              disabled={isBusy}
              onChange={(e) => {
                setText(e.target.value);
                handleResize();
              }}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder="Chat with Desk Officer, describe a bug, or paste / drop images, PDFs, HAR traces & log files..."
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
            disabled={isBusy}
            onClick={() => fileInputRef.current?.click()}
            className="btn-ghost p-2.5 rounded-lg disabled:opacity-50"
            title="Attach images, screenshots, PDF, DOCX, XLSX, HAR, logs, JSON, CSV, or code files"
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
            disabled={(!text.trim() && pendingFiles.length === 0) || isBusy}
            className="btn-primary flex items-center gap-2 px-4 min-h-[42px]"
            title={uploading ? 'Uploading files...' : 'Send message'}
          >
            {uploading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
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
