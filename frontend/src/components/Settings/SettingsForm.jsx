import React, { useState, useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';
import {
  Save,
  Key,
  Cpu,
  Bell,
  Globe,
  CheckCircle,
  Zap,
  AlertTriangle,
  Eye,
  Mail,
  Send,
  X,
} from 'lucide-react';
import toast from 'react-hot-toast';
import api from '../../services/api';
import useAppStore from '../../store/appStore';

const PROVIDER_DEFAULTS = {
  openai: 'gpt-4o-mini',
  gemini: 'gemini-3.1-flash-lite',
  anthropic: 'claude-3-5-sonnet-latest',
  ollama: 'llama3.1',
  openrouter: 'openai/gpt-4o-mini',
};

const PROVIDER_MODEL_PRESETS = {
  openai: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini'],
  gemini: ['gemini-3.1-flash-lite', 'gemini-3-flash-preview', 'gemini-flash-lite-latest', 'gemini-3.5-flash'],
  anthropic: ['claude-3-5-sonnet-latest', 'claude-3-5-haiku-latest'],
  openrouter: ['openai/gpt-4o-mini', 'google/gemini-3-flash-preview', 'anthropic/claude-3.5-sonnet'],
  ollama: ['llama3.1', 'qwen2.5', 'mistral'],
};

const SMTP_PRESETS = [
  {
    id: 'gmail',
    aliases: ['gmail', 'google'],
    label: 'Gmail (587 TLS)',
    host: 'smtp.gmail.com',
    port: '587',
    tls: 'true',
    ssl: 'false',
    usernameHint: 'you@gmail.com',
  },
  {
    id: 'office365',
    aliases: ['office365', 'outlook', 'm365', 'microsoft365', 'microsoft'],
    label: 'Microsoft 365 / Outlook',
    host: 'smtp.office365.com',
    port: '587',
    tls: 'true',
    ssl: 'false',
    usernameHint: 'you@company.com',
  },
  {
    id: 'zoho',
    aliases: ['zoho', 'zohomail'],
    label: 'Zoho Mail (587 TLS)',
    host: 'smtp.zoho.com',
    port: '587',
    tls: 'true',
    ssl: 'false',
    usernameHint: 'you@zohomail.com',
  },
  {
    id: 'sendgrid',
    aliases: ['sendgrid'],
    label: 'SendGrid / SSL (465)',
    host: 'smtp.sendgrid.net',
    port: '465',
    tls: 'false',
    ssl: 'true',
    usernameHint: 'apikey',
  },
];

function findSmtpPreset(presetParam) {
  if (!presetParam) return null;
  const normalized = presetParam.trim().toLowerCase();
  return (
    SMTP_PRESETS.find(
      (p) => p.id === normalized || (p.aliases && p.aliases.includes(normalized))
    ) || null
  );
}

export default function SettingsForm() {
  const location = useLocation();
  const setReporterName = useAppStore((s) => s.setReporterName);
  const emailPanelRef = useRef(null);
  const smtpHostRef = useRef(null);
  const smtpUsernameRef = useRef(null);
  const appliedDeepLinkRef = useRef('');

  const [settings, setSettings] = useState({
    llm_provider: 'openai',
    llm_api_key: '',
    llm_model: 'gpt-4o-mini',
    llm_base_url: '',
    jira_base_url: '',
    jira_email: '',
    jira_api_token: '',
    default_reporter: 'Mohamed Kamel',
    watcher_interval_minutes: '60',
    watcher_enabled: 'true',
    watcher_monitor_modules: 'true',
    watcher_monitor_agents: 'true',
    watcher_monitor_logs: 'true',
    notification_email_enabled: 'false',
    notification_email: '',
    smtp_host: '',
    smtp_port: '587',
    smtp_username: '',
    smtp_password: '',
    smtp_from_email: '',
    smtp_use_tls: 'true',
    smtp_use_ssl: 'false',
    notification_slack_enabled: 'false',
    notification_slack_webhook: '',
    notify_on_all_events: 'false',
    notify_on_changes: 'true',
    notify_on_success: 'false',
    notify_on_failures: 'true',
    browser_chromium: 'true',
    browser_firefox: 'true',
    browser_webkit: 'true',
    browser_headless: 'true',
  });

  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testingLlm, setTestingLlm] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testingNotif, setTestingNotif] = useState(null); // 'slack' | 'email' | 'all' | null
  const [notifTestResult, setNotifTestResult] = useState(null);
  const [showSmtpPass, setShowSmtpPass] = useState(false);
  const [showSlackWebhook, setShowSlackWebhook] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [highlightedSection, setHighlightedSection] = useState(null);
  const [urlPresetNotice, setUrlPresetNotice] = useState(null);

  useEffect(() => {
    let active = true;
    async function loadSettings() {
      try {
        const res = await api.getSettings();
        if (active && Array.isArray(res)) {
          const mapped = {};
          res.forEach((item) => {
            mapped[item.key] = item.value;
          });
          setSettings((prev) => ({
            ...prev,
            ...mapped,
            smtp_from_email: mapped.smtp_from_email || mapped.smtp_from || prev.smtp_from_email,
          }));
          if (mapped.default_reporter) {
            setReporterName(mapped.default_reporter);
          }
        }
      } catch (err) {
        if (active) {
          toast.error(err.message || 'Failed to load settings');
        }
      } finally {
        if (active) setSettingsLoaded(true);
      }
    }
    loadSettings();
    return () => {
      active = false;
    };
  }, [setReporterName]);

  useEffect(() => {
    if (!settingsLoaded) return undefined;

    const searchParams = new URLSearchParams(location.search);
    const sectionParam = (searchParams.get('section') || '').trim().toLowerCase();
    const presetParam = (searchParams.get('preset') || '').trim().toLowerCase();
    const hashValue = (location.hash || '').replace(/^#/, '').trim().toLowerCase();
    const matchedPreset = findSmtpPreset(presetParam);

    const wantsEmailAlerts =
      hashValue === 'email-alerts' ||
      sectionParam === 'email-alerts' ||
      Boolean(matchedPreset) ||
      Boolean(presetParam);

    if (!wantsEmailAlerts) {
      setHighlightedSection(null);
      return undefined;
    }

    const deepLinkKey = `${location.search}|${location.hash}`;
    if (appliedDeepLinkRef.current !== deepLinkKey) {
      appliedDeepLinkRef.current = deepLinkKey;
      setHighlightedSection('email-alerts');

      if (matchedPreset) {
        setSettings((prev) => ({
          ...prev,
          notification_email_enabled: 'true',
          smtp_host: matchedPreset.host,
          smtp_port: matchedPreset.port,
          smtp_use_tls: matchedPreset.tls,
          smtp_use_ssl: matchedPreset.ssl,
        }));
        setSaved(false);
        setNotifTestResult(null);
        setUrlPresetNotice({
          id: matchedPreset.id,
          label: matchedPreset.label,
          host: matchedPreset.host,
          port: matchedPreset.port,
        });
      } else if (presetParam) {
        setUrlPresetNotice({
          id: 'unknown',
          error: `Unknown SMTP preset "${presetParam}". Choose one of the quick presets below.`,
        });
      } else {
        setUrlPresetNotice({
          id: 'focus',
          label: 'Email Alerts (SMTP)',
        });
      }
    }

    const scrollTimer = setTimeout(() => {
      emailPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (matchedPreset) {
        smtpUsernameRef.current?.focus({ preventScroll: true });
      } else {
        smtpHostRef.current?.focus({ preventScroll: true });
      }
    }, 120);

    return () => clearTimeout(scrollTimer);
  }, [settingsLoaded, location.search, location.hash]);

  const handleChange = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
    if (key.startsWith('llm_')) setTestResult(null);
    if (
      key.startsWith('smtp_') ||
      key.startsWith('notification_') ||
      key.startsWith('notify_on_')
    ) {
      setNotifTestResult(null);
    }
  };

  const applySmtpPreset = (preset) => {
    setSettings((prev) => ({
      ...prev,
      notification_email_enabled: 'true',
      smtp_host: preset.host,
      smtp_port: preset.port,
      smtp_use_tls: preset.tls,
      smtp_use_ssl: preset.ssl,
    }));
    setUrlPresetNotice({
      id: preset.id,
      label: preset.label,
      host: preset.host,
      port: preset.port,
    });
    setSaved(false);
    setNotifTestResult(null);
  };

  const handleProviderChange = (provider) => {
    setSettings((prev) => {
      const currentModel = (prev.llm_model || '').trim();
      const allPresets = Object.values(PROVIDER_MODEL_PRESETS).flat();
      const isKnownDefault =
        !currentModel ||
        currentModel === 'gpt-4' ||
        currentModel === 'gemini-1.5-pro' ||
        currentModel === 'gemini-2.5-flash' ||
        currentModel === 'claude-3-opus' ||
        /\s/.test(currentModel) ||
        Object.values(PROVIDER_DEFAULTS).includes(currentModel) ||
        allPresets.includes(currentModel);
      return {
        ...prev,
        llm_provider: provider,
        llm_model: isKnownDefault ? PROVIDER_DEFAULTS[provider] || currentModel : currentModel,
      };
    });
    setSaved(false);
    setTestResult(null);
  };

  const handleTestLlm = async () => {
    setTestingLlm(true);
    setTestResult(null);
    try {
      const res = await api.testLlmConnection({
        llm_provider: settings.llm_provider,
        llm_api_key: settings.llm_api_key,
        llm_model: settings.llm_model,
        llm_base_url: settings.llm_base_url,
      });
      setTestResult(res);
    } catch (err) {
      setTestResult({ ok: false, error: err.message });
    } finally {
      setTestingLlm(false);
    }
  };

  const handleTestNotification = async (channel) => {
    setTestingNotif(channel);
    setNotifTestResult(null);
    try {
      const res = await api.testNotification({
        channel,
        notification_email_enabled: settings.notification_email_enabled,
        notification_email: (settings.notification_email || '').trim(),
        smtp_host: (settings.smtp_host || '').trim(),
        smtp_port: (settings.smtp_port || '587').trim(),
        smtp_username: (settings.smtp_username || '').trim(),
        smtp_password: settings.smtp_password || '',
        smtp_from_email: (settings.smtp_from_email || '').trim(),
        smtp_use_tls: settings.smtp_use_tls,
        smtp_use_ssl: settings.smtp_use_ssl,
        notification_slack_enabled: settings.notification_slack_enabled,
        notification_slack_webhook: (settings.notification_slack_webhook || '').trim(),
      });
      setNotifTestResult(res);
    } catch (err) {
      setNotifTestResult({ ok: false, channel, error: err.message });
    } finally {
      setTestingNotif(null);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const normalized = {
        ...settings,
        llm_api_key: (settings.llm_api_key || '').trim(),
        llm_model:
          (settings.llm_model || '').trim() && !/\s/.test((settings.llm_model || '').trim())
            ? settings.llm_model.trim()
            : PROVIDER_DEFAULTS[settings.llm_provider] || settings.llm_model,
        llm_base_url: (settings.llm_base_url || '').trim(),
        notification_email: (settings.notification_email || '').trim(),
        smtp_host: (settings.smtp_host || '').trim(),
        smtp_port: (settings.smtp_port || '587').trim(),
        smtp_username: (settings.smtp_username || '').trim(),
        smtp_from_email: (settings.smtp_from_email || '').trim(),
        notification_slack_webhook: (settings.notification_slack_webhook || '').trim(),
        notify_on_all_events: 'false',
        notify_on_success: 'false',
      };
      setSettings(normalized);
      await api.updateSettings(normalized);
      setReporterName(normalized.default_reporter);
      setSaved(true);
      setUrlPresetNotice(null);
      toast.success('Configuration saved successfully');
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      toast.error(err.message || 'Failed to save configuration');
    } finally {
      setLoading(false);
    }
  };

  const modelPresets = PROVIDER_MODEL_PRESETS[settings.llm_provider] || [];
  const hasInvalidModelSpaces = /\s/.test((settings.llm_model || '').trim());
  const smtpPasswordMasked = String(settings.smtp_password || '').startsWith('••••');
  const slackWebhookMasked = String(settings.notification_slack_webhook || '').startsWith('••••');
  const activeSmtpPreset = SMTP_PRESETS.find(
    (preset) =>
      (settings.smtp_host || '').trim().toLowerCase() === preset.host &&
      (settings.smtp_port || '').trim() === preset.port &&
      settings.smtp_use_tls === preset.tls &&
      settings.smtp_use_ssl === preset.ssl
  );

  return (
    <form onSubmit={handleSubmit} className="space-y-6 max-w-4xl pb-12">
      {saved && (
        <div className="flex items-center gap-2 p-3 bg-emerald-950 border border-emerald-800 text-emerald-300 rounded-xl text-xs font-semibold animate-in fade-in">
          <CheckCircle className="w-4 h-4" />
          Settings updated successfully! Watcher has logged this configuration change.
        </div>
      )}

      {/* LLM Engine Configuration */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 text-cyan-400">
            <Cpu className="w-5 h-5" />
            <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">AI / LLM Engine</h3>
          </div>
          <button
            type="button"
            onClick={handleTestLlm}
            disabled={testingLlm}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-700/60 text-cyan-300 text-xs font-semibold transition-all disabled:opacity-50"
          >
            <Zap className="w-3.5 h-3.5" />
            <span>{testingLlm ? 'Testing AI...' : 'Test AI Connection'}</span>
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Provider</label>
            <select
              value={settings.llm_provider}
              onChange={(e) => handleProviderChange(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            >
              <option value="openai">OpenAI (GPT-4o / GPT-4)</option>
              <option value="gemini">Google Gemini API</option>
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="openrouter">OpenRouter</option>
              <option value="ollama">Ollama (Local LLM)</option>
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">Model Name</label>
            <input
              type="text"
              list="llm-model-presets"
              value={settings.llm_model}
              onChange={(e) => handleChange('llm_model', e.target.value)}
              placeholder={`${PROVIDER_DEFAULTS[settings.llm_provider] || 'gpt-4o-mini'} (model ID only)`}
              className={`w-full bg-slate-950 border rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none ${
                hasInvalidModelSpaces ? 'border-amber-600' : 'border-slate-800'
              }`}
            />
            <datalist id="llm-model-presets">
              {modelPresets.map((preset) => (
                <option key={preset} value={preset} />
              ))}
            </datalist>
            {modelPresets.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {modelPresets.map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => handleChange('llm_model', preset)}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-mono border transition-colors ${
                      settings.llm_model === preset
                        ? 'bg-cyan-950/80 border-cyan-700 text-cyan-300'
                        : 'bg-slate-950/70 border-slate-800 text-slate-400 hover:text-cyan-300 hover:border-slate-700'
                    }`}
                  >
                    {preset}
                  </button>
                ))}
              </div>
            )}
            {hasInvalidModelSpaces && (
              <p className="text-[11px] text-amber-400 mt-1">
                Model IDs cannot contain spaces. Will default to{' '}
                <button
                  type="button"
                  onClick={() => handleChange('llm_model', PROVIDER_DEFAULTS[settings.llm_provider] || 'gemini-3.1-flash-lite')}
                  className="underline font-semibold text-cyan-300"
                >
                  {PROVIDER_DEFAULTS[settings.llm_provider]}
                </button>
                .
              </p>
            )}
          </div>

          <div>
            <label className="text-xs font-medium text-slate-400 mb-1.5 block">
              {settings.llm_provider === 'ollama' ? 'API Key (Not Required)' : 'API Key'}
            </label>
            <input
              type="password"
              value={settings.llm_api_key}
              onChange={(e) => handleChange('llm_api_key', e.target.value)}
              placeholder={settings.llm_provider === 'ollama' ? 'ollama (optional)' : 'sk-... / AQ... / AIza... (masked once saved)'}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
            />
          </div>
        </div>

        <div>
          <label className="text-xs font-medium text-slate-400 mb-1.5 block">
            Custom Base URL <span className="text-slate-600">(Optional — e.g. http://localhost:11434/v1 for Ollama or compatible gateway)</span>
          </label>
          <input
            type="text"
            value={settings.llm_base_url || ''}
            onChange={(e) => handleChange('llm_base_url', e.target.value)}
            placeholder={settings.llm_provider === 'ollama' ? 'http://localhost:11434/v1' : 'Leave blank for provider default'}
            className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
          />
        </div>

        {testResult && (
          <div
            className={`flex items-start gap-2 p-3 rounded-xl border text-xs ${
              testResult.ok
                ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                : 'bg-red-950/60 border-red-800 text-red-300'
            }`}
          >
            {testResult.ok ? (
              <CheckCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            ) : (
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            )}
            <div className="space-y-0.5">
              {testResult.ok ? (
                <>
                  <div className="font-semibold">
                    Connected to {testResult.provider}/{testResult.model} ({testResult.latency_ms}ms)
                  </div>
                  <div className="text-emerald-200/80">Reply: &quot;{testResult.reply}&quot;</div>
                </>
              ) : (
                <>
                  <div className="font-semibold">Connection failed ({testResult.provider}/{testResult.model})</div>
                  <div className="text-red-200/80">{testResult.error}</div>
                </>
              )}
            </div>
          </div>
        )}
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

        <div className="mt-4 pt-4 border-t border-slate-800 grid grid-cols-1 md:grid-cols-3 gap-4">
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
              Watcher Sweep Interval (Minutes)
            </label>
            <input
              type="number"
              min="1"
              max="1440"
              value={settings.watcher_interval_minutes}
              onChange={(e) => handleChange('watcher_interval_minutes', e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            />
          </div>

          <div className="flex items-end">
            <label className="w-full flex items-center justify-between p-2.5 rounded-lg bg-slate-950 border border-slate-800 text-xs text-slate-300 cursor-pointer">
              <span>Watcher Background Polling</span>
              <input
                type="checkbox"
                checked={settings.watcher_enabled === 'true'}
                onChange={(e) => handleChange('watcher_enabled', e.target.checked ? 'true' : 'false')}
                className="rounded text-cyan-500"
              />
            </label>
          </div>
        </div>
      </div>

      {/* Watcher Sentinel Scope */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 text-indigo-400">
            <Eye className="w-5 h-5" />
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">
                Watcher Telemetry &amp; Audit Scope
              </h3>
              <p className="text-[11px] text-slate-400">
                Records system-wide audit logs across modules and AI agents. External Slack &amp; Email alerts trigger strictly for Jira updates and AI agent failures.
              </p>
            </div>
          </div>

          <label className="flex items-center gap-2 text-xs font-semibold text-slate-200 cursor-pointer flex-shrink-0">
            <input
              type="checkbox"
              checked={settings.watcher_enabled === 'true'}
              onChange={(e) => handleChange('watcher_enabled', e.target.checked ? 'true' : 'false')}
              className="rounded border-slate-700 text-cyan-500"
            />
            <span>Watcher Active</span>
          </label>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <label className="flex items-start gap-2.5 p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.watcher_monitor_modules === 'true'}
              onChange={(e) => handleChange('watcher_monitor_modules', e.target.checked ? 'true' : 'false')}
              className="mt-0.5 rounded text-cyan-500"
            />
            <div>
              <div className="font-semibold text-slate-100">Module Audit Trail</div>
              <div className="text-[11px] text-slate-400 mt-0.5">
                Log Projects, Skills, Bugs, Uploads, Settings &amp; Jira sync telemetry internally
              </div>
            </div>
          </label>

          <label className="flex items-start gap-2.5 p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.watcher_monitor_agents === 'true'}
              onChange={(e) => handleChange('watcher_monitor_agents', e.target.checked ? 'true' : 'false')}
              className="mt-0.5 rounded text-cyan-500"
            />
            <div>
              <div className="font-semibold text-slate-100">Every AI Agent</div>
              <div className="text-[11px] text-slate-400 mt-0.5">
                Desk Officer, Detective, Coroner, Judge, Sheriff, Sanity Testers
              </div>
            </div>
          </label>

          <label className="flex items-start gap-2.5 p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={settings.watcher_monitor_logs === 'true'}
              onChange={(e) => handleChange('watcher_monitor_logs', e.target.checked ? 'true' : 'false')}
              className="mt-0.5 rounded text-cyan-500"
            />
            <div>
              <div className="font-semibold text-slate-100">Full Log Capture</div>
              <div className="text-[11px] text-slate-400 mt-0.5">
                Capture API diagnostics, status sweeps &amp; Jira transitions in Logs &amp; Timeline
              </div>
            </div>
          </label>
        </div>
      </div>

      {/* External Notifications (Email SMTP & Slack Webhook) */}
      <div
        id="external-notifications"
        className="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 shadow space-y-5"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 text-amber-400">
            <Bell className="w-5 h-5" />
            <div>
              <h3 className="text-sm font-semibold uppercase tracking-wider text-slate-100">
                External Notifications (Slack &amp; Email)
              </h3>
              <p className="text-[11px] text-slate-400">
                Strict Watcher Scope: alerts dispatch ONLY for Jira updates and AI agent failures.
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => handleTestNotification('all')}
            disabled={testingNotif !== null}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-700/60 text-amber-300 text-xs font-semibold transition-all disabled:opacity-50"
          >
            <Send className="w-3.5 h-3.5" />
            <span>{testingNotif === 'all' ? 'Sending Tests...' : 'Test All Channels'}</span>
          </button>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Email SMTP Panel */}
          <div
            id="email-alerts"
            ref={emailPanelRef}
            className={`p-4 bg-slate-950/60 border rounded-xl space-y-3 scroll-mt-6 transition-all duration-300 ${
              highlightedSection === 'email-alerts'
                ? 'border-cyan-500 ring-2 ring-cyan-500/40 shadow-lg shadow-cyan-950/50'
                : 'border-slate-800'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Mail className="w-4 h-4 text-cyan-400" />
                <span className="text-xs font-semibold text-slate-200">Email Alerts (SMTP)</span>
              </div>
              <div className="flex items-center gap-2.5">
                <button
                  type="button"
                  onClick={() => handleTestNotification('email')}
                  disabled={testingNotif !== null}
                  className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-[11px] font-semibold text-cyan-300 disabled:opacity-50"
                >
                  {testingNotif === 'email' ? 'Sending...' : 'Test Email'}
                </button>
                <input
                  type="checkbox"
                  checked={settings.notification_email_enabled === 'true'}
                  onChange={(e) => handleChange('notification_email_enabled', e.target.checked ? 'true' : 'false')}
                  className="rounded border-slate-700 text-cyan-500"
                  title="Enable Email Alerts (SMTP)"
                />
              </div>
            </div>

            {urlPresetNotice && (
              <div
                className={`flex items-start justify-between gap-2 px-2.5 py-2 rounded-lg border text-[11px] ${
                  urlPresetNotice.error
                    ? 'bg-amber-950/50 border-amber-800 text-amber-300'
                    : 'bg-cyan-950/50 border-cyan-800/80 text-cyan-200'
                }`}
              >
                <div>
                  {urlPresetNotice.error ? (
                    <span>{urlPresetNotice.error}</span>
                  ) : urlPresetNotice.host ? (
                    <span>
                      Quick preset selected: <strong>{urlPresetNotice.label}</strong> (
                      <code className="font-mono text-cyan-300">
                        {urlPresetNotice.host}:{urlPresetNotice.port}
                      </code>
                      ). Enter SMTP Username &amp; Password, click <strong>Test Email</strong>, then{' '}
                      <strong>Save Configuration</strong>.
                    </span>
                  ) : (
                    <span>
                      Select one of the quick SMTP presets below, enter SMTP Username &amp; Password, then click{' '}
                      <strong>Save Configuration</strong>.
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setUrlPresetNotice(null)}
                  className="text-slate-400 hover:text-slate-200 flex-shrink-0"
                  aria-label="Dismiss preset notice"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">
                Recipient Email(s) <span className="text-slate-600">(comma-separated for multiple)</span>
              </label>
              <input
                type="text"
                placeholder="you@company.com, lead@company.com"
                value={settings.notification_email}
                onChange={(e) => handleChange('notification_email', e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
              />
            </div>

            {/* Quick SMTP Presets */}
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {SMTP_PRESETS.map((preset) => {
                const isActive = activeSmtpPreset?.id === preset.id;
                return (
                  <button
                    key={preset.id}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => applySmtpPreset(preset)}
                    className={`px-2 py-0.5 rounded text-[10px] border transition-colors ${
                      isActive
                        ? 'bg-cyan-950/80 border-cyan-500 text-cyan-300 font-semibold shadow-sm shadow-cyan-950/50'
                        : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-cyan-300 hover:border-slate-700'
                    }`}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div className="col-span-2">
                <label className="text-[11px] text-slate-400 block mb-1">SMTP Host</label>
                <input
                  ref={smtpHostRef}
                  type="text"
                  placeholder="smtp.gmail.com"
                  value={settings.smtp_host}
                  onChange={(e) => handleChange('smtp_host', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-400 block mb-1">Port</label>
                <input
                  type="number"
                  placeholder="587"
                  value={settings.smtp_port}
                  onChange={(e) => handleChange('smtp_port', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[11px] text-slate-400 block mb-1">SMTP Username</label>
                <input
                  ref={smtpUsernameRef}
                  type="text"
                  placeholder={activeSmtpPreset?.usernameHint || 'sender@company.com'}
                  value={settings.smtp_username}
                  onChange={(e) => handleChange('smtp_username', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[11px] text-slate-400 block mb-1">
                  SMTP Password{' '}
                  {!smtpPasswordMasked && (
                    <button
                      type="button"
                      onClick={() => setShowSmtpPass((v) => !v)}
                      className="text-cyan-400 hover:underline ml-1"
                    >
                      {showSmtpPass ? 'Hide' : 'Show'}
                    </button>
                  )}
                </label>
                <input
                  type={showSmtpPass && !smtpPasswordMasked ? 'text' : 'password'}
                  placeholder="App password (masked once saved)"
                  value={settings.smtp_password}
                  onChange={(e) => handleChange('smtp_password', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
                />
              </div>
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">
                From Email <span className="text-slate-600">(optional, defaults to SMTP Username)</span>
              </label>
              <input
                type="email"
                placeholder="qa-watcher@company.com"
                value={settings.smtp_from_email}
                onChange={(e) => handleChange('smtp_from_email', e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
              />
            </div>

            <div className="flex items-center gap-5 pt-1 text-[11px] text-slate-300">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={settings.smtp_use_tls === 'true'}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setSettings((prev) => ({
                      ...prev,
                      smtp_use_tls: checked ? 'true' : 'false',
                      smtp_use_ssl: checked ? 'false' : prev.smtp_use_ssl,
                    }));
                    setSaved(false);
                    setNotifTestResult(null);
                  }}
                  className="rounded border-slate-700 text-cyan-500"
                />
                <span>Use STARTTLS (Port 587)</span>
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={settings.smtp_use_ssl === 'true'}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setSettings((prev) => ({
                      ...prev,
                      smtp_use_ssl: checked ? 'true' : 'false',
                      smtp_use_tls: checked ? 'false' : prev.smtp_use_tls,
                    }));
                    setSaved(false);
                    setNotifTestResult(null);
                  }}
                  className="rounded border-slate-700 text-cyan-500"
                />
                <span>Use SSL/TLS (Port 465)</span>
              </label>
            </div>
          </div>

          {/* Slack & Event Subscriptions */}
          <div className="space-y-4">
            <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-200">Slack Webhook</span>
                <div className="flex items-center gap-2.5">
                  <button
                    type="button"
                    onClick={() => handleTestNotification('slack')}
                    disabled={testingNotif !== null}
                    className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-[11px] font-semibold text-cyan-300 disabled:opacity-50"
                  >
                    {testingNotif === 'slack' ? 'Sending...' : 'Test Slack'}
                  </button>
                  <input
                    type="checkbox"
                    checked={settings.notification_slack_enabled === 'true'}
                    onChange={(e) => handleChange('notification_slack_enabled', e.target.checked ? 'true' : 'false')}
                    className="rounded border-slate-700 text-cyan-500"
                  />
                </div>
              </div>

              <div>
                <label className="text-[11px] text-slate-400 block mb-1">
                  Incoming Webhook URL{' '}
                  {!slackWebhookMasked && (
                    <button
                      type="button"
                      onClick={() => setShowSlackWebhook((v) => !v)}
                      className="text-cyan-400 hover:underline ml-1"
                    >
                      {showSlackWebhook ? 'Hide' : 'Show'}
                    </button>
                  )}
                </label>
                <input
                  type={showSlackWebhook && !slackWebhookMasked ? 'url' : 'password'}
                  placeholder="https://hooks.slack.com/services/... (masked once saved)"
                  value={settings.notification_slack_webhook}
                  onChange={(e) => handleChange('notification_slack_webhook', e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none font-mono"
                />
              </div>
            </div>

            {/* Event Triggers — Strict Scope */}
            <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-xl space-y-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-slate-200">
                  Slack &amp; Email Trigger Scope
                </span>
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-950/80 border border-indigo-800/70 text-indigo-300 font-semibold">
                  Jira + AI Failures Only
                </span>
              </div>

              <div className="space-y-2.5">
                <label className="flex items-start gap-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={settings.notify_on_changes === 'true'}
                    onChange={(e) => handleChange('notify_on_changes', e.target.checked ? 'true' : 'false')}
                    className="mt-0.5 rounded text-cyan-500"
                  />
                  <span>
                    <strong className="text-slate-100">Jira Updates</strong> — Ticket created in Backlog, Jira transitions (<code className="text-cyan-300">In QA</code>, <code className="text-emerald-300">Ready To Deploy</code>, <code className="text-red-300">Failed QA</code>), board status drift &amp; evidence sync
                  </span>
                </label>

                <label className="flex items-start gap-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={settings.notify_on_failures === 'true'}
                    onChange={(e) => handleChange('notify_on_failures', e.target.checked ? 'true' : 'false')}
                    className="mt-0.5 rounded text-cyan-500"
                  />
                  <span>
                    <strong className="text-slate-100">AI Agent Failures</strong> — LLM execution errors, agent runtime failures, and Jira poll/sync errors
                  </span>
                </label>
              </div>

              <p className="text-[11px] text-slate-500 pt-1 border-t border-slate-800/70">
                Project/Skill edits, file uploads, chat turns, and non-Jira agent runs stay in the internal Audit Log without external spam.
              </p>
            </div>
          </div>
        </div>

        {/* Live Test Delivery Banner */}
        {notifTestResult && (
          <div
            className={`p-3.5 rounded-xl border text-xs space-y-1.5 ${
              notifTestResult.ok
                ? 'bg-emerald-950/60 border-emerald-800 text-emerald-300'
                : 'bg-red-950/60 border-red-800 text-red-300'
            }`}
          >
            <div className="flex items-center gap-2 font-semibold">
              {notifTestResult.ok ? (
                <CheckCircle className="w-4 h-4 flex-shrink-0" />
              ) : (
                <AlertTriangle className="w-4 h-4 flex-shrink-0" />
              )}
              <span>
                {notifTestResult.ok
                  ? `Test notification sent via: ${(notifTestResult.channels_sent || []).join(', ')}`
                  : 'Test notification failed'}
              </span>
            </div>

            {notifTestResult.error && (
              <div className="text-red-200/80 pl-6">{notifTestResult.error}</div>
            )}

            {notifTestResult.slack?.attempted && (
              <div className="pl-6 text-[11px]">
                <strong>Slack:</strong>{' '}
                {notifTestResult.slack.sent
                  ? 'Delivered to webhook'
                  : `Failed (${notifTestResult.slack.error || notifTestResult.slack.reason})`}
              </div>
            )}

            {notifTestResult.email?.attempted && (
              <div className="pl-6 text-[11px]">
                <strong>Email:</strong>{' '}
                {notifTestResult.email.sent
                  ? `Delivered to ${(notifTestResult.email.recipients || []).join(', ')}`
                  : `Failed (${notifTestResult.email.error || notifTestResult.email.reason})`}
              </div>
            )}
          </div>
        )}
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
          className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold transition-all shadow-lg shadow-cyan-900/30 disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          <span>{loading ? 'Saving Settings...' : 'Save Configuration'}</span>
        </button>
      </div>
    </form>
  );
}
