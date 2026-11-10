const BASE_URL = '/api';

async function request(url, options = {}) {
  const config = {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  };

  if (config.body && typeof config.body === 'object' && !(config.body instanceof FormData)) {
    config.body = JSON.stringify(config.body);
  }

  const res = await fetch(`${BASE_URL}${url}`, config);

  if (!res.ok) {
    const errorBody = await res.text().catch(() => '');
    throw new Error(errorBody || `Request failed: ${res.status} ${res.statusText}`);
  }

  const contentType = res.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return res.json();
  }
  return res;
}

const api = {
  // Projects
  getProjects: () => request('/projects'),
  getProject: (id) => request(`/projects/${id}`),
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
  sendMessage: (data) => request('/chat', { method: 'POST', body: data }),

  // Logs
  getLogs: (params = {}) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.append(k, v);
    });
    const queryString = qs.toString();
    return request(`/logs${queryString ? `?${queryString}` : ''}`);
  },

  // Settings
  getSettings: () => request('/settings'),
  updateSettings: (data) => request('/settings', { method: 'PUT', body: data }),

  // Summary
  getSummary: (projectId) => request(`/reports/summary/${projectId}`),

  // Evidence
  getEvidenceUrl: (id) => `${BASE_URL}/evidence/${id}/file`,
};

export default api;
