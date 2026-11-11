const BASE_URL = '/api';

async function request(url, options = {}) {
  const isFormData = options.body instanceof FormData;
  const config = {
    ...options,
    headers: isFormData
      ? { ...options.headers }
      : { 'Content-Type': 'application/json', ...options.headers },
  };

  if (config.body && typeof config.body === 'object' && !isFormData) {
    config.body = JSON.stringify(config.body);
  }

  const res = await fetch(`${BASE_URL}${url}`, config);

  if (!res.ok) {
    let message = `Request failed: ${res.status} ${res.statusText}`;
    const errorBody = await res.text().catch(() => '');
    if (errorBody) {
      try {
        const parsed = JSON.parse(errorBody);
        message = parsed.detail || errorBody;
      } catch {
        message = errorBody;
      }
    }
    throw new Error(message);
  }

  const contentType = res.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return res.json();
  }
  return res;
}

const api = {
  // Projects & Assets
  getProjects: () => request('/projects'),
  getProject: (id) => request(`/projects/${id}`),
  getProjectAssets: (id) => request(`/projects/${id}/assets`),
  createProject: (data) => request('/projects', { method: 'POST', body: data }),
  updateProject: (id, data) => request(`/projects/${id}`, { method: 'PUT', body: data }),
  deleteProject: (id) => request(`/projects/${id}`, { method: 'DELETE' }),

  // Skills
  getSkills: (projectId) => request(`/projects/${projectId}/skills`),
  createSkill: (projectId, data) =>
    request(`/projects/${projectId}/skills`, { method: 'POST', body: data }),
  deleteSkill: (id) => request(`/skills/${id}`, { method: 'DELETE' }),

  // Bugs
  getBugs: (projectId) => request(`/projects/${projectId}/bugs`),
  getBug: (id) => request(`/bugs/${id}`),
  retestBug: (id) => request(`/bugs/${id}/retest`, { method: 'POST' }),
  raiseBugToJira: (id) => request(`/bugs/${id}/raise-jira`, { method: 'POST' }),
  updateBugStatus: (id, status) => request(`/bugs/${id}/status`, { method: 'PUT', body: { status } }),
  autoTest: (projectId, data) =>
    request(`/projects/${projectId}/bugs/auto-test`, { method: 'POST', body: data }),

  // Chat
  getChatHistory: (projectId) => request(`/chat/${projectId}/history`),
  clearChatHistory: (projectId) => request(`/chat/${projectId}/history`, { method: 'DELETE' }),
  uploadChatFiles: (projectId, fileList) => {
    const formData = new FormData();
    Array.from(fileList || []).forEach((file) => {
      formData.append('files', file);
    });
    if (projectId) {
      formData.append('project_id', String(projectId));
    }
    return request('/chat/upload', { method: 'POST', body: formData });
  },
  sendMessage: (data) => request('/chat', { method: 'POST', body: data }),

  // Logs & Watcher Telemetry
  getLogs: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.append(k, v);
    });
    const queryString = qs.toString();
    return request(`/logs${queryString ? `?${queryString}` : ''}`);
  },
  getLogsSummary: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.append(k, v);
    });
    const queryString = qs.toString();
    return request(`/logs/summary${queryString ? `?${queryString}` : ''}`);
  },
  triggerWatcherScan: (data = {}) =>
    request('/logs/watcher-scan', { method: 'POST', body: data }),

  // Settings
  getLlmStatus: () => request('/settings/llm-status'),
  testLlmConnection: (data) => request('/settings/test-llm', { method: 'POST', body: data }),
  testNotification: (data = {}) => request('/settings/test-notification', { method: 'POST', body: data }),
  getSettings: () => request('/settings'),
  updateSettings: (data) => request('/settings', { method: 'PUT', body: data }),

  // Summary
  getSummary: (projectId) => request(`/reports/summary/${projectId}`),

  // Evidence
  getEvidenceUrl: (id) => `${BASE_URL}/evidence/${id}/file`,
};

export default api;
