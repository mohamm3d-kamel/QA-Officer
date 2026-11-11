import { create } from 'zustand';

const useAppStore = create((set, get) => ({
  // Sidebar
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

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
    set((s) => ({
      projects: s.projects.filter((p) => p.id !== id),
      selectedProject: s.selectedProject?.id === id ? null : s.selectedProject,
    })),

  // Chat
  messages: [],
  chatLoading: false,
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setMessages: (messages) => set({ messages }),
  setChatLoading: (loading) => set({ chatLoading: loading }),
  appendToLastMessage: (content) =>
    set((s) => {
      const msgs = [...s.messages];
      if (msgs.length > 0) {
        const last = { ...msgs[msgs.length - 1] };
        last.content = (last.content || '') + content;
        msgs[msgs.length - 1] = last;
      }
      return { messages: msgs };
    }),
  upsertStreamChunk: (streamId, delta) =>
    set((s) => {
      if (!streamId || !delta) return s;
      const idx = s.messages.findIndex((m) => m.streamId === streamId);
      if (idx === -1) {
        return {
          messages: [
            ...s.messages,
            {
              id: streamId,
              streamId,
              sender: 'agent',
              agent: 'Desk Officer',
              content: delta,
              isStreaming: true,
              timestamp: new Date().toISOString(),
            },
          ],
        };
      }
      const msgs = [...s.messages];
      msgs[idx] = {
        ...msgs[idx],
        content: (msgs[idx].content || '') + delta,
        isStreaming: true,
      };
      return { messages: msgs };
    }),
  finalizeStreamMessage: (streamId, finalMessage) =>
    set((s) => {
      const idx = streamId ? s.messages.findIndex((m) => m.streamId === streamId) : -1;
      if (idx === -1) {
        return {
          messages: [...s.messages, { ...finalMessage, isStreaming: false }],
        };
      }
      const msgs = [...s.messages];
      msgs[idx] = {
        ...msgs[idx],
        ...finalMessage,
        isStreaming: false,
      };
      return { messages: msgs };
    }),
  endStreamIndicator: (streamId) =>
    set((s) => {
      if (!streamId) return s;
      return {
        messages: s.messages.map((m) =>
          m.streamId === streamId ? { ...m, isStreaming: false } : m
        ),
      };
    }),

  // Agent statuses
  agentStatuses: {},
  setAgentStatus: (agent, status, message, bugId) =>
    set((s) => ({
      agentStatuses: {
        ...s.agentStatuses,
        [agent]: { status, message, bugId },
      },
    })),
  clearAgentStatuses: () => set({ agentStatuses: {} }),

  // Skills
  skills: [],
  setSkills: (skills) => set({ skills }),
  addSkill: (skill) => set((s) => ({ skills: [...s.skills, skill] })),
  removeSkill: (id) =>
    set((s) => ({ skills: s.skills.filter((sk) => sk.id !== id) })),

  // Bugs
  bugs: [],
  setBugs: (bugs) => set({ bugs }),

  // Logs & Watcher Telemetry
  logs: [],
  setLogs: (logs) => set({ logs }),
  wsConnected: false,
  setWsConnected: (wsConnected) => set({ wsConnected }),
  watcherEvents: [],
  addWatcherEvent: (event) =>
    set((s) => {
      if (!event) return s;
      const filtered = event.id
        ? s.watcherEvents.filter((e) => e.id !== event.id)
        : s.watcherEvents;
      return {
        watcherEvents: [event, ...filtered].slice(0, 120),
      };
    }),
  clearWatcherEvents: () => set({ watcherEvents: [] }),

  // Notifications
  notifications: [],
  addNotification: (notification) =>
    set((s) => ({
      notifications: [notification, ...s.notifications].slice(0, 50),
    })),
  clearNotifications: () => set({ notifications: [] }),

  // Right sidebar in chat
  chatSidebarOpen: true,
  toggleChatSidebar: () =>
    set((s) => ({ chatSidebarOpen: !s.chatSidebarOpen })),
}));

export default useAppStore;
