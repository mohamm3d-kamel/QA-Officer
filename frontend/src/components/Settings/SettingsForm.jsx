import React, { useState, useEffect } from 'react';
import { Save, Key, Cpu, Tag, Bell, Globe, CheckCircle } from 'lucide-react';
import api from '../../services/api';

export default function SettingsForm() {
  const [settings, setSettings] = useState({
    llm_provider: 'openai',
    llm_api_key: '',
    llm_model: 'gpt-4',
    jira_base_url: '',
    jira_email: '',
    jira_api_token: '',
    default_reporter: 'Mohamed Kamel',
    watcher_interval_minutes: '60',
    watcher_enabled: 'true',
    notification_email_enabled: 'false',
    notification_email: '',
    notification_slack_enabled: 'false',
    notification_slack_webhook: '',
    browser_chromium: 'true',
    browser_firefox: 'true',
    browser_webkit: 'true',
    browser_headless: 'true',
  });

  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    async function loadSettings() {
      try {
        const res = await api.getSettings();
        if (Array.isArray(res)) {
          const mapped = {};
          res.forEach((item) => {
            mapped[item.key] = item.value;
          });
          setSettings((prev) => ({ ...prev, ...mapped }));
        }
      } catch (err) {
        console.error('Failed to load settings:', err);
      }
    }
    loadSettings();
  }, []);

  const handleChange = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.updateSettings(settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      console.error('Failed to update settings:', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6 max-w-4xl pb-12">
      {saved && (
        <div className="flex items-center gap-2 p-3 bg-emerald-950 border border-emerald-800 text-emerald-300 rounded-xl text-xs font-semibold animate-in fade-in">
          <CheckCircle className="w-4 h-4" />
          Settings updated successfully!
        </div>
      )}

      {/* LLM Engine Configuration */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow">
        <div className="flex items-center gap-2.5 mb-4 text-cyan-400">
          <Cpu className="w-5 h-5" />
          <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">AI / LLM Engine</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Provider</label>
            <select
              value={settings.llm_provider}
              onChange={(e) => handleChange('llm_provider', e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            >
              <option value="openai">OpenAI (GPT-4 / GPT-4o)</option>
              <option value="gemini">Google Gemini API</option>
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="ollama">Ollama (Local LLM)</option>
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Model Name</label>
            <input
              type="text"
              value={settings.llm_model}
              onChange={(e) => handleChange('llm_model', e.target.value)}
              placeholder="gpt-4, gemini-1.5-pro, claude-3-opus..."
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">API Key</label>
            <input
              type="password"
              value={settings.llm_api_key}
              onChange={(e) => handleChange('llm_api_key', e.target.value)}
              placeholder="sk-..."
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
            />
          </div>
        </div>
      </div>

      {/* Jira Cloud Integration */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow">
        <div className="flex items-center gap-2.5 mb-4 text-blue-400">
          <Key className="w-5 h-5" />
          <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">Jira Cloud Integration</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Jira Base URL</label>
            <input
              type="url"
              value={settings.jira_base_url}
              onChange={(e) => handleChange('jira_base_url', e.target.value)}
              placeholder="https://company.atlassian.net"
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Jira Email</label>
            <input
              type="email"
              value={settings.jira_email}
              onChange={(e) => handleChange('jira_email', e.target.value)}
              placeholder="tester@company.com"
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Jira API Token</label>
            <input
              type="password"
              value={settings.jira_api_token}
              onChange={(e) => handleChange('jira_api_token', e.target.value)}
              placeholder="Atlassian API Token"
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
            />
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-slate-800 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Default Reporter Name</label>
            <input
              type="text"
              value={settings.default_reporter}
              onChange={(e) => handleChange('default_reporter', e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">
              Watcher Polling Interval (Minutes)
            </label>
            <input
              type="number"
              min="5"
              max="1440"
              value={settings.watcher_interval_minutes}
              onChange={(e) => handleChange('watcher_interval_minutes', e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>
        </div>
      </div>

      {/* Notifications (Email & Slack) */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow">
        <div className="flex items-center gap-2.5 mb-4 text-amber-400">
          <Bell className="w-5 h-5" />
          <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">External Notifications</h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-200">Email Alerts</span>
              <input
                type="checkbox"
                checked={settings.notification_email_enabled === 'true'}
                onChange={(e) => handleChange('notification_email_enabled', e.target.checked ? 'true' : 'false')}
                className="rounded border-slate-700 text-cyan-500"
              />
            </div>
            <input
              type="email"
              placeholder="notification@example.com"
              value={settings.notification_email}
              onChange={(e) => handleChange('notification_email', e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2"
            />
          </div>

          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-200">Slack Webhook</span>
              <input
                type="checkbox"
                checked={settings.notification_slack_enabled === 'true'}
                onChange={(e) => handleChange('notification_slack_enabled', e.target.checked ? 'true' : 'false')}
                className="rounded border-slate-700 text-cyan-500"
              />
            </div>
            <input
              type="url"
              placeholder="https://hooks.slack.com/services/..."
              value={settings.notification_slack_webhook}
              onChange={(e) => handleChange('notification_slack_webhook', e.target.value)}
              className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2"
            />
          </div>
        </div>
      </div>

      {/* Browser Engines */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow">
        <div className="flex items-center gap-2.5 mb-4 text-teal-400">
          <Globe className="w-5 h-5" />
          <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">Playwright Multi-Browser Engine</h3>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <label className="flex items-center gap-2 p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.browser_chromium === 'true'}
              onChange={(e) => handleChange('browser_chromium', e.target.checked ? 'true' : 'false')}
              className="rounded text-cyan-500"
            />
            Chromium (Chrome)
          </label>

          <label className="flex items-center gap-2 p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.browser_firefox === 'true'}
              onChange={(e) => handleChange('browser_firefox', e.target.checked ? 'true' : 'false')}
              className="rounded text-cyan-500"
            />
            Firefox (Gecko)
          </label>

          <label className="flex items-center gap-2 p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.browser_webkit === 'true'}
              onChange={(e) => handleChange('browser_webkit', e.target.checked ? 'true' : 'false')}
              className="rounded text-cyan-500"
            />
            WebKit (Safari)
          </label>

          <label className="flex items-center gap-2 p-3 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.browser_headless === 'true'}
              onChange={(e) => handleChange('browser_headless', e.target.checked ? 'true' : 'false')}
              className="rounded text-cyan-500"
            />
            Headless Mode
          </label>
        </div>
      </div>

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={loading}
          className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition-all shadow-lg shadow-cyan-900/30"
        >
          <Save className="w-4 h-4" />
          <span>{loading ? 'Saving Settings...' : 'Save Configuration'}</span>
        </button>
      </div>
    </form>
  );
}

