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

  // Logs
  logs: [],
  setLogs: (logs) => set({ logs }),

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
