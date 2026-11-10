import { create } from 'zustand';

const STORAGE_KEY = 'qa_officer_chat_v1';
const MAX_STORED_PER_PROJECT = 60;

function loadStoredMessages() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function persistMessages(messagesByProject) {
  try {
    const trimmed = {};
    Object.entries(messagesByProject).forEach(([pid, list]) => {
      if (Array.isArray(list)) {
        trimmed[pid] = list.filter((m) => !m.isStatus).slice(-MAX_STORED_PER_PROJECT);
      }
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // Ignore storage quota or private-mode failures cleanly
  }
}

const resolveKey = (projectId, selectedProject) =>
  String(projectId ?? selectedProject?.id ?? 'default');

const useAppStore = create((set, get) => ({
  // Sidebar
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  // User display name synced from Settings (default_reporter)
  reporterName: 'Mohamed Kamel',
  setReporterName: (reporterName) =>
    set({ reporterName: reporterName || 'Mohamed Kamel' }),

  // Projects
  projects: [],
  selectedProject: null,
  setProjects: (projects) => set({ projects }),
  setSelectedProject: (project) => set({ selectedProject: project }),
  addProject: (project) =>
    set((s) => ({ projects: [...s.projects, project] })),
  updateProject: (id, data) =>
    set((s) => ({
      projects: s.projects.map((p) => (p.id === id ? { ...p, ...data } : p)),
      selectedProject:
        s.selectedProject?.id === id
          ? { ...s.selectedProject, ...data }
          : s.selectedProject,
    })),
  removeProject: (id) =>
    set((s) => {
      const nextByProj = { ...s.messagesByProject };
      delete nextByProj[String(id)];
      persistMessages(nextByProj);
      return {
        projects: s.projects.filter((p) => p.id !== id),
        selectedProject: s.selectedProject?.id === id ? null : s.selectedProject,
        messagesByProject: nextByProj,
      };
    }),

  // Chat (isolated & persisted per project)
  messagesByProject: loadStoredMessages(),
  chatLoading: false,
  addMessage: (msg, projectId) =>
    set((s) => {
      const pid = resolveKey(projectId, s.selectedProject);
      let prevList = s.messagesByProject[pid] || [];
      const last = prevList[prevList.length - 1];
      if (
        msg.isStatus &&
        last?.isStatus &&
        last.agent === msg.agent &&
        last.content === msg.content
      ) {
        return s;
      }
      if (!msg.isStatus && msg.sender === 'agent' && (msg.agent || '').toLowerCase() === 'desk officer') {
        prevList = prevList.filter((m) => !(m.isStatus && m.agent === 'desk_officer'));
      }
      const nextMap = {
        ...s.messagesByProject,
        [pid]: [...prevList, msg],
      };
      if (!msg.isStatus) persistMessages(nextMap);
      return { messagesByProject: nextMap };
    }),
  setMessages: (messages, projectId) =>
    set((s) => {
      const pid = resolveKey(projectId, s.selectedProject);
      const nextMap = { ...s.messagesByProject, [pid]: messages };
      persistMessages(nextMap);
      return { messagesByProject: nextMap };
    }),
  clearProjectMessages: (projectId) =>
    set((s) => {
      const pid = resolveKey(projectId, s.selectedProject);
      const nextMap = { ...s.messagesByProject, [pid]: [] };
      persistMessages(nextMap);
      return { messagesByProject: nextMap };
    }),
  setChatLoading: (loading) => set({ chatLoading: loading }),
  appendToLastMessage: (content, projectId) =>
    set((s) => {
      const pid = resolveKey(projectId, s.selectedProject);
      const msgs = [...(s.messagesByProject[pid] || [])];
      if (msgs.length > 0) {
        const last = { ...msgs[msgs.length - 1] };
        last.content = (last.content || '') + content;
        msgs[msgs.length - 1] = last;
      }
      const nextMap = { ...s.messagesByProject, [pid]: msgs };
      persistMessages(nextMap);
      return { messagesByProject: nextMap };
    }),

  // Agent statuses (isolated per project)
  agentStatusesByProject: {},
  setAgentStatus: (agent, status, message, bugId, projectId) =>
    set((s) => {
      const pid = resolveKey(projectId, s.selectedProject);
      const current = s.agentStatusesByProject[pid] || {};
      return {
        agentStatusesByProject: {
          ...s.agentStatusesByProject,
          [pid]: {
            ...current,
            [agent]: { status, message, bugId },
          },
        },
      };
    }),
  clearAgentStatuses: (projectId) =>
    set((s) => {
      const pid = resolveKey(projectId, s.selectedProject);
      return {
        agentStatusesByProject: {
          ...s.agentStatusesByProject,
          [pid]: {},
        },
      };
    }),

  // Skills
  skills: [],
  setSkills: (skills) => set({ skills }),
  addSkill: (skill) => set((s) => ({ skills: [skill, ...s.skills] })),
  removeSkill: (id) =>
    set((s) => ({ skills: s.skills.filter((sk) => sk.id !== id) })),

  // Bugs
  bugs: [],
  setBugs: (bugs) => set({ bugs }),
  removeBug: (id) =>
    set((s) => ({ bugs: s.bugs.filter((b) => b.id !== id) })),

  // Logs
  logs: [],
  setLogs: (logs) => set({ logs }),

  // Notifications
  notifications: [],
  addNotification: (notification) =>
    set((s) => ({
      notifications: [
        {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
          timestamp: new Date().toISOString(),
          read: false,
          ...notification,
        },
        ...s.notifications,
      ].slice(0, 50),
    })),
  markAllNotificationsRead: () =>
    set((s) => ({
      notifications: s.notifications.map((n) => (n.read ? n : { ...n, read: true })),
    })),
  clearNotifications: () => set({ notifications: [] }),

  // Right sidebar in chat
  chatSidebarOpen: true,
  toggleChatSidebar: () =>
    set((s) => ({ chatSidebarOpen: !s.chatSidebarOpen })),
}));

export default useAppStore;
