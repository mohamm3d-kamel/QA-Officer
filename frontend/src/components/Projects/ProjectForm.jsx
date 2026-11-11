import React, { useState, useEffect } from 'react';
import { X, FolderGit2, Save, FileText, Eraser, KeyRound, Plus, Trash2, Eye, EyeOff } from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../services/api';
import useAppStore from '../../store/appStore';

const REPORT_FORMAT_PRESETS = [
  {
    id: 'clean-fields',
    label: 'Clean Section List',
    template: [
      'Title',
      'Environment',
      'Description',
      'Steps to reproduce',
      'Expected Behavior',
      'Actual Behavior',
      'Evidence',
    ].join('\n'),
  },
  {
    id: 'standard-md',
    label: 'Standard QA Markdown',
    template: [
      '## Title',
      '[Concise bug title]',
      '',
      '## Environment',
      '[Project / URL / Browser / Component]',
      '',
      '## Description',
      '[Clear summary of the issue]',
      '',
      '## Steps to Reproduce',
      '1. [Step 1]',
      '2. [Step 2]',
      '',
      '## Expected Behavior',
      '[Expected outcome]',
      '',
      '## Actual Behavior',
      '[Observed outcome]',
      '',
      '## Root Cause',
      '[Confirmed root cause]',
      '',
      '## Evidence',
      '[Screenshots, videos, or logs]',
    ].join('\n'),
  },
];

export default function ProjectForm({ isOpen, onClose, initialData = null }) {
  const addProject = useAppStore((s) => s.addProject);
  const updateProject = useAppStore((s) => s.updateProject);

  const [name, setName] = useState('');
  const [jiraKey, setJiraKey] = useState('QA');
  const [jiraUrl, setJiraUrl] = useState('');
  const [appUrl, setAppUrl] = useState('');
  const [repoUrl, setRepoUrl] = useState('');
  const [description, setDescription] = useState('');
  const [reportFormat, setReportFormat] = useState('');
  const [credentials, setCredentials] = useState([]);
  const [revealedRows, setRevealedRows] = useState({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (initialData) {
      setName(initialData.name || '');
      setJiraKey(initialData.jira_project_key || 'QA');
      setJiraUrl(initialData.jira_base_url || '');
      setAppUrl(initialData.app_url || '');
      setRepoUrl(initialData.repo_url || '');
      setDescription(initialData.description || '');
      setReportFormat(initialData.report_format || '');
      const initialCreds = Array.isArray(initialData.environment_credentials)
        ? initialData.environment_credentials.map((c) => ({
            role: c?.role || '',
            username: c?.username || '',
            password: c?.password || '',
          }))
        : [];
      setCredentials(initialCreds);
      setRevealedRows({});
    } else {
      setName('');
      setJiraKey('QA');
      setJiraUrl('');
      setAppUrl('');
      setRepoUrl('');
      setDescription('');
      setReportFormat('');
      setCredentials([]);
      setRevealedRows({});
    }
  }, [initialData, isOpen]);

  if (!isOpen) return null;

  const isMaskedSecret = (val) => typeof val === 'string' && val.includes('••••');

  const handleAddCredential = () => {
    setCredentials((prev) => [...prev, { role: '', username: '', password: '' }]);
  };

  const handleCredentialChange = (index, field, nextValue) => {
    setCredentials((prev) =>
      prev.map((item, idx) => {
        if (idx !== index) return item;
        if (field === 'password' && isMaskedSecret(item.password) && nextValue !== item.password) {
          if (nextValue.startsWith(item.password)) {
            return { ...item, password: nextValue.slice(item.password.length) };
          }
          if (isMaskedSecret(nextValue)) {
            return { ...item, password: '' };
          }
        }
        return { ...item, [field]: nextValue };
      })
    );
  };

  const handleRemoveCredential = (index) => {
    setCredentials((prev) => prev.filter((_, idx) => idx !== index));
    setRevealedRows({});
  };

  const toggleRevealRow = (index) => {
    setRevealedRows((prev) => ({ ...prev, [index]: !prev[index] }));
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;

    setLoading(true);
    const cleanedCredentials = credentials
      .map((c) => ({
        role: (c.role || '').trim(),
        username: (c.username || '').trim(),
        password: c.password || '',
      }))
      .filter((c) => c.role || c.username || c.password);

    const payload = {
      name,
      jira_project_key: jiraKey,
      jira_base_url: jiraUrl,
      app_url: appUrl,
      repo_url: repoUrl,
      description,
      report_format: reportFormat,
      environment_credentials: cleanedCredentials,
    };

    try {
      if (initialData?.id) {
        const updated = await api.updateProject(initialData.id, payload);
        updateProject(initialData.id, updated);
        toast.success('Project configuration updated');
      } else {
        const created = await api.createProject(payload);
        addProject(created);
        toast.success('Project created');
      }
      onClose();
    } catch (err) {
      console.error('Project save error:', err);
      toast.error(err?.message || 'Failed to save project configuration');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-950/60 shrink-0">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-cyan-950 text-cyan-400 border border-cyan-800">
              <FolderGit2 className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-slate-100">
                {initialData ? 'Project Configuration' : 'New QA Project'}
              </h3>
              <p className="text-xs text-slate-400">
                Configure project details, Jira connections, and Report Format
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 rounded text-slate-400 hover:text-slate-200">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto flex-1">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block">
              Project Name *
            </label>
            <input
              type="text"
              required
              placeholder="e.g. Core Banking Platform"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block">
                Jira Project Key *
              </label>
              <input
                type="text"
                required
                placeholder="e.g. QA, CORE, SCRUM"
                value={jiraKey}
                onChange={(e) => setJiraKey(e.target.value.toUpperCase())}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 font-mono uppercase focus:border-cyan-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block">
                Jira Base URL
              </label>
              <input
                type="url"
                placeholder="https://company.atlassian.net"
                value={jiraUrl}
                onChange={(e) => setJiraUrl(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block">
                Web App URL (SUT)
              </label>
              <input
                type="url"
                placeholder="https://test.app.com"
                value={appUrl}
                onChange={(e) => setAppUrl(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block">
                Repository URL
              </label>
              <input
                type="url"
                placeholder="https://github.com/org/repo"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 block">
              Description / Business Context
            </label>
            <textarea
              rows={2}
              placeholder="High-level project scope, modules, and target architectures..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          {/* Environment Role Credentials Section */}
          <div className="pt-4 border-t border-slate-800 space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-amber-400" />
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-200">
                  Environment Credentials
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-950/70 text-amber-300 border border-amber-800/60">
                  {credentials.length} {credentials.length === 1 ? 'Role' : 'Roles'}
                </span>
              </div>

              <button
                type="button"
                onClick={handleAddCredential}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-medium bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700 transition-colors"
              >
                <Plus className="w-3 h-3" />
                <span>Add Role</span>
              </button>
            </div>

            {credentials.length === 0 ? (
              <div className="rounded-lg border border-dashed border-slate-800 bg-slate-950/60 px-3 py-2.5 text-[11px] text-slate-400">
                No role credentials configured yet. Add Tenant, Merchant, Operation, or Customer Service logins for automated QA verification.
              </div>
            ) : (
              <div className="space-y-2">
                {credentials.map((cred, idx) => {
                  const masked = isMaskedSecret(cred.password);
                  const showPlain = Boolean(revealedRows[idx]) && !masked;
                  return (
                    <div
                      key={idx}
                      className="grid grid-cols-1 sm:grid-cols-12 gap-2 items-center bg-slate-950/70 border border-slate-800/90 rounded-lg p-2"
                    >
                      <input
                        type="text"
                        placeholder="Role (e.g. Tenant)"
                        aria-label={`Credential ${idx + 1} Role`}
                        value={cred.role}
                        onChange={(e) => handleCredentialChange(idx, 'role', e.target.value)}
                        className="sm:col-span-3 bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-2.5 py-1.5 focus:border-cyan-500 focus:outline-none"
                      />
                      <input
                        type="text"
                        placeholder="Username / Email"
                        aria-label={`Credential ${idx + 1} Username`}
                        value={cred.username}
                        onChange={(e) => handleCredentialChange(idx, 'username', e.target.value)}
                        className="sm:col-span-4 bg-slate-900 border border-slate-800 rounded text-xs font-mono text-slate-200 px-2.5 py-1.5 focus:border-cyan-500 focus:outline-none"
                      />
                      <div className="sm:col-span-4 relative">
                        <input
                          type={showPlain ? 'text' : 'password'}
                          placeholder={masked ? 'Saved (masked)' : 'Password'}
                          aria-label={`Credential ${idx + 1} Password`}
                          value={cred.password}
                          onChange={(e) => handleCredentialChange(idx, 'password', e.target.value)}
                          className="w-full bg-slate-900 border border-slate-800 rounded text-xs font-mono text-slate-200 pl-2.5 pr-7 py-1.5 focus:border-cyan-500 focus:outline-none"
                        />
                        {!masked && cred.password && (
                          <button
                            type="button"
                            onClick={() => toggleRevealRow(idx)}
                            title={showPlain ? 'Hide password' : 'Show password'}
                            className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-cyan-300 p-0.5"
                          >
                            {showPlain ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                          </button>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => handleRemoveCredential(idx)}
                        title="Remove role credential"
                        aria-label={`Remove credential ${idx + 1}`}
                        className="sm:col-span-1 flex items-center justify-center p-1.5 rounded text-slate-400 hover:text-red-400 hover:bg-slate-800 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            <p className="text-[11px] text-slate-400">
              Passwords are stored securely and masked after saving. Automated Playwright verification selects the matching role login when navigating your SUT URL.
            </p>
          </div>

          {/* Dedicated Report Format Section */}
          <div className="pt-4 border-t border-slate-800 space-y-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-emerald-400" />
                <label
                  htmlFor="project-report-format"
                  className="text-xs font-semibold uppercase tracking-wider text-slate-200"
                >
                  Report Format
                </label>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-950/70 text-emerald-300 border border-emerald-800/60">
                  Followed as-is by Agents
                </span>
              </div>

              <div className="flex items-center gap-1.5">
                {REPORT_FORMAT_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => setReportFormat(preset.template)}
                    className="px-2 py-1 rounded-md text-[11px] font-medium bg-slate-800 hover:bg-slate-700 text-cyan-300 border border-slate-700 transition-colors"
                  >
                    {preset.label}
                  </button>
                ))}
                {reportFormat.trim() && (
                  <button
                    type="button"
                    onClick={() => setReportFormat('')}
                    title="Clear custom format (revert to default)"
                    className="p-1 rounded-md text-slate-400 hover:text-red-300 hover:bg-slate-800 transition-colors"
                  >
                    <Eraser className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>

            <textarea
              id="project-report-format"
              rows={7}
              placeholder={
                'Define your exact bug report structure (plain section list or Markdown), e.g.:\nTitle\nEnvironment\nDescription\nSteps to reproduce\nExpected Behavior\nActual Behavior\nEvidence'
              }
              value={reportFormat}
              onChange={(e) => setReportFormat(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-xs font-mono text-slate-200 px-3 py-2.5 leading-relaxed focus:border-emerald-500 focus:outline-none"
            />
            <p className="text-[11px] text-slate-400">
              Detective will strictly preserve these exact section names, order, and style during bug report creation (and Judge verifies against them when requested) — with no unrequested sections or formatting changes.
            </p>
          </div>

          <div className="pt-3 border-t border-slate-800 flex justify-end gap-3 shrink-0">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-xs font-medium text-slate-400 hover:text-slate-200 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition-all disabled:opacity-50"
            >
              <Save className="w-3.5 h-3.5" />
              <span>{loading ? 'Saving...' : initialData ? 'Save Configuration' : 'Create Project'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
