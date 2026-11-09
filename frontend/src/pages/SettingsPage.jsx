import React from 'react';
import { Settings as SettingsIcon } from 'lucide-react';
import SettingsForm from '../components/Settings/SettingsForm';

export default function SettingsPage() {
  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="p-2.5 rounded-xl bg-cyan-950 text-cyan-400 border border-cyan-800">
          <SettingsIcon className="w-6 h-6" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-slate-100">System Configuration</h1>
          <p className="text-xs text-slate-400 mt-1">
            Configure LLM engines, Jira Cloud authentication, default reporter, Watcher intervals, and notifications.
          </p>
        </div>
      </div>

      <SettingsForm />
    </div>
  );
}

