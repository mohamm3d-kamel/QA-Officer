import React, { useEffect, useMemo, useState } from 'react';
import { Bot, X, Play, CheckCircle2 } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../services/api';
import useAppStore from '../../store/appStore';

const FALLBACK_MODULES = [
  'Authentication & Session',
  'Checkout & Payment Gateway',
  'Shopping Cart & Discounts',
  'Core API & Validation',
];

export default function AutoTestModal({ isOpen, onClose, projectId, onComplete }) {
  const skills = useAppStore((s) => s.skills);
  const bugs = useAppStore((s) => s.bugs);
  const addNotification = useAppStore((s) => s.addNotification);

  const availableModules = useMemo(() => {
    const seen = new Set();
    const result = [];
    const addMod = (raw) => {
      const clean = (raw || '').trim();
      if (!clean || seen.has(clean.toLowerCase())) return;
      seen.add(clean.toLowerCase());
      result.push(clean);
    };

    skills
      .filter((s) => s.type !== 'report_format')
      .forEach((s) => addMod(s.title?.split('-')[0]));
    bugs.forEach((b) => addMod(b.module));
    FALLBACK_MODULES.forEach(addMod);
    return result;
  }, [skills, bugs]);

  const [scope, setScope] = useState('all');
  const [selectedModules, setSelectedModules] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setSelectedModules(availableModules.slice(0, 3));
    }
  }, [isOpen, availableModules]);

  if (!isOpen) return null;

  const toggleModule = (mod) => {
    if (selectedModules.includes(mod)) {
      setSelectedModules(selectedModules.filter((m) => m !== mod));
    } else {
      setSelectedModules([...selectedModules, mod]);
    }
  };

  const handleStartAutoTest = async () => {
    if (scope === 'selected' && selectedModules.length === 0) {
      toast.error('Select at least one target module or switch to All Requirements');
      return;
    }

    setLoading(true);
    try {
      const res = await api.autoTest(projectId, {
        scope,
        modules: scope === 'selected' ? selectedModules : null,
      });

      const summaryMsg = `Detective found ${res.discovered_count} defect(s) and created ${res.processed_pipeline_count} report(s)${
        res.skipped_duplicates ? ` (${res.skipped_duplicates} duplicate skipped)` : ''
      }.`;
      toast.success(summaryMsg);
      addNotification({
        title: 'Autonomous Testing Completed',
        message: summaryMsg,
        level: 'success',
      });

      if (onComplete) await onComplete(res);
      onClose();
    } catch (err) {
      console.error('Auto test error:', err);
      toast.error(err.message || 'Auto-Test failed');
      addNotification({
        title: 'Auto Test Failed',
        message: err.message,
        level: 'error',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-950/60">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-cyan-950 text-cyan-400 border border-cyan-800">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-slate-100">Autonomous Bug Discovery</h3>
              <p className="text-xs text-slate-400">Launch autonomous agent testing</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 rounded text-slate-400 hover:text-slate-200">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-5">
          {/* Scope selection */}
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2 block">
              Test Scope
            </label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setScope('all')}
                className={`p-3 rounded-xl border text-left transition-all ${
                  scope === 'all'
                    ? 'border-cyan-500 bg-cyan-950/40 text-cyan-200 ring-2 ring-cyan-500/20'
                    : 'border-slate-800 bg-slate-950/50 text-slate-400 hover:border-slate-700'
                }`}
              >
                <div className="text-xs font-semibold text-slate-200">All Requirements</div>
                <div className="text-[11px] text-slate-400 mt-1">Scan entire system and all stored business skills</div>
              </button>

              <button
                type="button"
                onClick={() => setScope('selected')}
                className={`p-3 rounded-xl border text-left transition-all ${
                  scope === 'selected'
                    ? 'border-cyan-500 bg-cyan-950/40 text-cyan-200 ring-2 ring-cyan-500/20'
                    : 'border-slate-800 bg-slate-950/50 text-slate-400 hover:border-slate-700'
                }`}
              >
                <div className="text-xs font-semibold text-slate-200">Selected Modules</div>
                <div className="text-[11px] text-slate-400 mt-1">Focus test coverage on specific high-risk areas</div>
              </button>
            </div>
          </div>

          {/* Module checklist if selected */}
          {scope === 'selected' && (
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2 block">
                Target Modules
              </label>
              <div className="space-y-1.5 max-h-40 overflow-y-auto pr-1">
                {availableModules.map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => toggleModule(m)}
                    className="w-full flex items-center justify-between p-2 rounded-lg bg-slate-950/40 border border-slate-800 hover:border-slate-700 text-left text-xs transition-colors"
                  >
                    <span className="text-slate-300">{m}</span>
                    {selectedModules.includes(m) ? (
                      <CheckCircle2 className="w-4 h-4 text-cyan-400" />
                    ) : (
                      <div className="w-4 h-4 rounded border border-slate-700" />
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800 text-[11px] text-slate-400 space-y-1">
            <div className="text-slate-300 font-medium">Pipeline Actions:</div>
            <div>• Detective analyzes requirements to uncover defects &amp; root cause</div>
            <div>• Detective writes clean bug reports matching your Project Report Format</div>
            <div>• Coroner (visual capture), Judge (verification), and Sheriff (Jira Backlog) remain on standby until you explicitly ask</div>
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-800 bg-slate-950/60 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-slate-200 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleStartAutoTest}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition-all disabled:opacity-50 shadow-lg shadow-cyan-900/30"
          >
            {loading ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                <span>Autonomous Agents Running...</span>
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>Start Autonomous Test</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
