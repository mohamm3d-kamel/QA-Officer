import React, { useState } from 'react';
import { BookOpen, Plus, Trash2 } from 'lucide-react';
import useAppStore from '../../store/appStore';
import api from '../../services/api';

export default function SkillPanel({ projectId }) {
  const skills = useAppStore((s) => s.skills);
  const addSkill = useAppStore((s) => s.addSkill);
  const removeSkill = useAppStore((s) => s.removeSkill);
  const setSelectedProject = useAppStore((s) => s.setSelectedProject);
  const updateProject = useAppStore((s) => s.updateProject);

  const [isAdding, setIsAdding] = useState(false);
  const [type, setType] = useState('requirement');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSaveSkill = async (e) => {
    e.preventDefault();
    if (!title.trim() || !content.trim()) return;

    setLoading(true);
    try {
      const saved = await api.createSkill(projectId, { type, title, content });
      addSkill(saved);
      if (type === 'report_format') {
        const refreshed = await api.getProject(projectId).catch(() => null);
        if (refreshed) {
          updateProject(refreshed.id, refreshed);
          setSelectedProject(refreshed);
        }
      }
      setTitle('');
      setContent('');
      setIsAdding(false);
    } catch (err) {
      console.error('Failed to save skill:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await api.deleteSkill(id);
      removeSkill(id);
    } catch (err) {
      console.error('Failed to delete skill:', err);
    }
  };

  return (
    <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-4 shadow-lg backdrop-blur">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-cyan-400" />
          <h3 className="text-sm font-semibold text-slate-200">Stored Skills & Rules</h3>
        </div>
        <button
          onClick={() => setIsAdding(!isAdding)}
          className="p-1 rounded-md bg-slate-800 hover:bg-slate-700 text-cyan-400 hover:text-cyan-300 transition-colors"
          title="Add Skill / Format"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>

      <p className="text-xs text-slate-400 mb-3">
        Requirements &amp; report formats stored here guide all AI agents (also editable in Project Config).
      </p>

      {/* Add form */}
      {isAdding && (
        <form onSubmit={handleSaveSkill} className="mb-4 p-3 bg-slate-950 border border-cyan-800/60 rounded-lg space-y-2.5">
          <div>
            <label className="text-[11px] font-medium text-slate-400">Skill Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="w-full mt-1 bg-slate-900 border border-slate-700 rounded text-xs text-slate-200 px-2 py-1.5 focus:border-cyan-500 focus:outline-none"
            >
              <option value="requirement">Business Requirement / User Story</option>
              <option value="report_format">Bug Report Format Template</option>
              <option value="business_doc">Business Logic Document</option>
              <option value="test_case">Acceptance Criteria</option>
            </select>
          </div>

          <div>
            <label className="text-[11px] font-medium text-slate-400">Title</label>
            <input
              type="text"
              placeholder="e.g. Checkout Validation Rule"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full mt-1 bg-slate-900 border border-slate-700 rounded text-xs text-slate-200 px-2 py-1.5 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-[11px] font-medium text-slate-400">Content / Template</label>
            <textarea
              rows={3}
              placeholder="Paste user story, specification, or exact bug report markdown structure..."
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="w-full mt-1 bg-slate-900 border border-slate-700 rounded text-xs text-slate-200 px-2 py-1.5 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setIsAdding(false)}
              className="px-2.5 py-1 text-xs text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-3 py-1 bg-cyan-600 hover:bg-cyan-500 text-white rounded text-xs font-medium transition-colors"
            >
              {loading ? 'Saving...' : 'Save Skill'}
            </button>
          </div>
        </form>
      )}

      {/* Skills list */}
      <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
        {skills.length === 0 ? (
          <div className="text-center py-4 text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
            No skills stored yet. Click + to add your requirements or report format.
          </div>
        ) : (
          skills.map((skill) => (
            <div
              key={skill.id}
              className="p-2.5 rounded-lg bg-slate-950/60 border border-slate-800/90 hover:border-slate-700 flex items-start justify-between gap-2 group transition-all"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <span
                    className={`text-[9px] uppercase px-1.5 py-0.2 rounded font-semibold ${
                      skill.type === 'report_format'
                        ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                        : 'bg-cyan-950 text-cyan-400 border border-cyan-800'
                    }`}
                  >
                    {skill.type.replace('_', ' ')}
                  </span>
                  <span className="text-xs font-medium text-slate-200 truncate">{skill.title}</span>
                </div>
                <p className="text-[11px] text-slate-400 line-clamp-2">{skill.content}</p>
              </div>

              <button
                onClick={() => handleDelete(skill.id)}
                className="opacity-0 group-hover:opacity-100 p-1 text-slate-500 hover:text-red-400 transition-opacity"
                title="Delete skill"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
