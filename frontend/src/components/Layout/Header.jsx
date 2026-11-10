import { useLocation } from 'react-router-dom';
import { Bell, Trash2 } from 'lucide-react';
import useAppStore from '../../store/appStore';
import api from '../../services/api';
import { useState, useRef, useEffect } from 'react';

const pageTitles = {
  '/': 'Dashboard',
  '/projects': 'Projects',
  '/logs': 'Agent Logs',
  '/settings': 'Settings',
};

export default function Header() {
  const location = useLocation();
  const notifications = useAppStore((s) => s.notifications);
  const markAllNotificationsRead = useAppStore((s) => s.markAllNotificationsRead);
  const clearNotifications = useAppStore((s) => s.clearNotifications);
  const reporterName = useAppStore((s) => s.reporterName);
  const setReporterName = useAppStore((s) => s.setReporterName);
  const [showNotifs, setShowNotifs] = useState(false);
  const notifRef = useRef(null);

  let title = pageTitles[location.pathname] || '';
  if (location.pathname.startsWith('/chat/')) title = 'Chat';
  if (location.pathname.startsWith('/summary/')) title = 'Summary Report';

  useEffect(() => {
    api
      .getSettings()
      .then((s) => {
        if (s?.default_reporter) setReporterName(s.default_reporter);
      })
      .catch(() => {});
  }, [setReporterName]);

  useEffect(() => {
    function handleClick(e) {
      if (notifRef.current && !notifRef.current.contains(e.target)) {
        setShowNotifs(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const unread = notifications.filter((n) => !n.read).length;
  const avatarInitial = (reporterName || 'M').trim().charAt(0).toUpperCase() || 'M';

  const handleToggleNotifs = () => {
    const nextOpen = !showNotifs;
    setShowNotifs(nextOpen);
    if (nextOpen && unread > 0) {
      markAllNotificationsRead();
    }
  };

  return (
    <header className="flex items-center justify-between h-16 px-6 border-b border-slate-700/50 bg-surface-800/50 backdrop-blur-sm flex-shrink-0">
      <div>
        <h1 className="text-lg font-semibold text-white">{title}</h1>
      </div>

      <div className="flex items-center gap-3">
        {/* Notifications */}
        <div className="relative" ref={notifRef}>
          <button
            onClick={handleToggleNotifs}
            aria-label="Notifications"
            className="relative p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-700/50 transition-colors"
          >
            <Bell className="w-5 h-5" />
            {unread > 0 && (
              <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full bg-brand-500 text-[10px] font-bold flex items-center justify-center text-white">
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </button>

          {showNotifs && (
            <div className="absolute right-0 top-full mt-2 w-80 max-h-96 overflow-y-auto glass-card shadow-2xl z-50 animate-fade-in">
              <div className="p-3 border-b border-slate-700/50 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-white">Notifications</h3>
                {notifications.length > 0 && (
                  <button
                    onClick={clearNotifications}
                    className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 px-2 py-1 rounded hover:bg-slate-700/40 transition-colors"
                  >
                    <Trash2 className="w-3 h-3" />
                    Clear all
                  </button>
                )}
              </div>
              {notifications.length === 0 ? (
                <div className="p-6 text-center text-slate-500 text-sm">
                  No notifications yet
                </div>
              ) : (
                <div className="divide-y divide-slate-700/30">
                  {notifications.slice(0, 20).map((n) => (
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
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-white truncate">{n.title}</p>
                          <p className="text-xs text-slate-400 mt-0.5">{n.message}</p>
                          <p className="text-[10px] text-slate-500 mt-1">
                            {new Date(n.timestamp).toLocaleTimeString()}
                          </p>
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
            {avatarInitial}
          </div>
          <span className="text-sm font-medium text-slate-300 hidden sm:block">
            {reporterName || 'Mohamed Kamel'}
          </span>
        </div>
      </div>
    </header>
  );
}
