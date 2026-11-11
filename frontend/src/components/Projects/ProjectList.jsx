import React from 'react';
import { useNavigate } from 'react-router-dom';
import { FolderGit2, ExternalLink, MessageSquare, Trash2, Edit2, FileText, KeyRound } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../services/api';
import useAppStore from '../../store/appStore';

export default function ProjectList({ onEditProject }) {
  const projects = useAppStore((s) => s.projects);
  const removeProject = useAppStore((s) => s.removeProject);
  const navigate = useNavigate();

  const handleDelete = async (e, id) => {
    e.stopPropagation();
    if (!window.confirm('Are you sure you want to delete this project and all its skills/bugs?')) return;
    try {
      await api.deleteProject(id);
      removeProject(id);
      toast.success('Project deleted');
    } catch (err) {
      console.error('Delete error:', err);
      toast.error(err?.message || 'Failed to delete project');
    }
  };

  if (!projects || projects.length === 0) {
    return (
      <div className="text-center py-16 border-2 border-dashed border-slate-800 rounded-2xl bg-slate-900/30">
        <FolderGit2 className="w-12 h-12 text-slate-600 mx-auto mb-3" />
        <h3 className="text-base font-semibold text-slate-300">No Projects Found</h3>
        <p className="text-xs text-slate-500 max-w-sm mx-auto mt-1">
          Create your first QA project to configure Jira keys, URLs, and business requirements.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
      {projects.map((proj) => (
        <div
          key={proj.id}
          onClick={() => navigate(`/chat/${proj.id}`)}
          className="group relative bg-slate-900/80 hover:bg-slate-900 border border-slate-800 hover:border-cyan-500/50 rounded-2xl p-5 shadow-lg transition-all duration-200 cursor-pointer flex flex-col justify-between"
        >
          <div>
            <div className="flex items-start justify-between gap-3 mb-3">
              <div className="flex items-center gap-2.5">
                <div className="p-2 rounded-xl bg-cyan-950 text-cyan-400 border border-cyan-800 group-hover:scale-105 transition-transform">
                  <FolderGit2 className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-slate-100 group-hover:text-cyan-300 transition-colors">
                    {proj.name}
                  </h3>
                  <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                    <span className="text-[11px] font-mono text-cyan-400 bg-cyan-950/60 px-1.5 py-0.5 rounded border border-cyan-900/50">
                      JIRA: {proj.jira_project_key || 'QA'}
                    </span>
                    {proj.report_format && (
                      <span
                        title="Custom Project Report Format configured"
                        className="inline-flex items-center gap-1 text-[10px] font-medium text-emerald-300 bg-emerald-950/60 px-1.5 py-0.5 rounded border border-emerald-900/50"
                      >
                        <FileText className="w-2.5 h-2.5" />
                        <span>Report Format</span>
                      </span>
                    )}
                    {Array.isArray(proj.environment_credentials) && proj.environment_credentials.length > 0 && (
                      <span
                        title={`Configured roles: ${proj.environment_credentials
                          .map((c) => c.role || c.username)
                          .filter(Boolean)
                          .join(', ')}`}
                        className="inline-flex items-center gap-1 text-[10px] font-medium text-amber-300 bg-amber-950/60 px-1.5 py-0.5 rounded border border-amber-900/50"
                      >
                        <KeyRound className="w-2.5 h-2.5" />
                        <span>
                          {proj.environment_credentials.length}{' '}
                          {proj.environment_credentials.length === 1 ? 'Role' : 'Roles'}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1 opacity-75 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (onEditProject) onEditProject(proj);
                  }}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-cyan-300 hover:bg-slate-800 transition-colors"
                  title="Project Configuration & Report Format"
                  aria-label={`Configure ${proj.name}`}
                >
                  <Edit2 className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={(e) => handleDelete(e, proj.id)}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-red-400 hover:bg-slate-800 transition-colors"
                  title="Delete Project"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <p className="text-xs text-slate-400 line-clamp-2 mb-4">
              {proj.description || 'No description provided.'}
            </p>
          </div>

          <div className="pt-4 border-t border-slate-800/80 flex items-center justify-between text-xs text-slate-500">
            <div className="truncate max-w-[180px]">
              {proj.app_url ? (
                <span className="text-slate-400 hover:text-cyan-400 flex items-center gap-1">
                  <ExternalLink className="w-3 h-3" />
                  {proj.app_url.replace(/^https?:\/\//, '')}
                </span>
              ) : (
                'No target URL'
              )}
            </div>

            <button
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/chat/${proj.id}`);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-cyan-600/10 text-cyan-400 group-hover:bg-cyan-600 group-hover:text-white transition-all font-medium text-xs"
            >
              <MessageSquare className="w-3.5 h-3.5" />
              <span>QA Chat</span>
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
