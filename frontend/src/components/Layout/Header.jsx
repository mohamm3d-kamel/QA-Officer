import { useLocation } from 'react-router-dom';
import { Bell, Eye } from 'lucide-react';
import useAppStore from '../../store/appStore';
import { useState, useRef, useEffect } from 'react';

const pageTitles = {
  '/': 'Dashboard',
  '/projects': 'Projects',
  '/logs': 'Watcher & Agent Logs',
  '/settings': 'Settings',
};

export default function Header() {
  const location = useLocation();
  const notifications = useAppStore((s) => s.notifications);
  const clearNotifications = useAppStore((s) => s.clearNotifications);
  const wsConnected = useAppStore((s) => s.wsConnected);
  const [showNotifs, setShowNotifs] = useState(false);
  const notifRef = useRef(null);

  let title = pageTitles[location.pathname] || '';
  if (location.pathname.startsWith('/chat/')) title = 'Chat';
  if (location.pathname.startsWith('/summary/')) title = 'Summary Report';

  useEffect(() => {
    function handleClick(e) {
      if (notifRef.current && !notifRef.current.contains(e.target)) {
        setShowNotifs(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const unread = notifications.filter(
    (n) => Date.now() - new Date(n.timestamp).getTime() < 60000
  ).length;

  return (
    <header className="flex items-center justify-between h-16 px-6 border-b border-slate-700/50 bg-surface-800/50 backdrop-blur-sm flex-shrink-0">
      <div>
        <h1 className="text-lg font-semibold text-white">{title}</h1>
      </div>

      <div className="flex items-center gap-3">
        {/* Live Watcher Telemetry Status */}
        <div
          className={`hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11px] font-medium ${
            wsConnected
              ? 'bg-emerald-950/60 border-emerald-800/70 text-emerald-300'
              : 'bg-slate-900 border-slate-700 text-slate-400'
          }`}
          title="Watcher real-time WebSocket stream"
        >
          <Eye className="w-3.5 h-3.5" />
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              wsConnected ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'
            }`}
          />
          <span>{wsConnected ? 'Watcher Live' : 'Reconnecting...'}</span>
        </div>

        {/* Notifications */}
        <div className="relative" ref={notifRef}>
          <button
            onClick={() => setShowNotifs(!showNotifs)}
            className="relative p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
            title="Watcher Alerts & Notifications"
          >
            <Bell className="w-5 h-5" />
            {unread > 0 && (
              <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-brand-500 text-[10px] font-bold flex items-center justify-center text-white">
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </button>

          {showNotifs && (
            <div className="absolute right-0 top-full mt-2 w-96 max-h-96 overflow-y-auto glass-card shadow-2xl z-50 animate-fade-in">
              <div className="p-3 border-b border-slate-700/50 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-white">Watcher Notifications</h3>
                {notifications.length > 0 && (
                  <button
                    onClick={clearNotifications}
                    className="text-[11px] text-cyan-400 hover:underline"
                  >
                    Clear
                  </button>
                )}
              </div>
              {notifications.length === 0 ? (
                <div className="p-6 text-center text-slate-500 text-sm">
                  No notifications yet
                </div>
              ) : (
                <div className="divide-y divide-slate-700/30">
                  {notifications.slice(0, 25).map((n) => (
                    <div key={n.id} className="p-3 hover:bg-slate-700/30 transition-colors">
                      <div className="flex items-start gap-2">
                        <span
                          className={`mt-1 w-2 h-2 rounded-full flex-shrink-0 ${
                            n.level === 'error'
                              ? 'bg-red-400'
                              : n.level === 'success'
                              ? 'bg-green-400'
                              : n.level === 'warning'
                              ? 'bg-yellow-400'
                              : 'bg-blue-400'
                          }`}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-semibold text-white truncate">{n.title}</p>
                          <p className="text-xs text-slate-300 mt-0.5">{n.message}</p>
                          <div className="flex items-center gap-2 text-[10px] text-slate-500 mt-1">
                            <span>{new Date(n.timestamp).toLocaleTimeString()}</span>
                            {n.module && <span>· {n.module}</span>}
                            {n.jira_key && <span>· {n.jira_key}</span>}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* User avatar */}
        <div className="flex items-center gap-2 pl-3 border-l border-slate-700/50">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-brand-400 to-brand-600 flex items-center justify-center text-white text-sm font-bold">
            M
          </div>
          <span className="text-sm font-medium text-slate-300 hidden sm:block">Mohamed</span>
        </div>
      </div>
    </header>
  );
}
