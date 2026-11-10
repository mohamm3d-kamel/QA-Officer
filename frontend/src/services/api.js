const BASE_URL = '/api';

async function request(url, options = {}) {
  const { timeoutMs = 120000, headers: customHeaders = {}, ...rest } = options;
  const isFormData = typeof FormData !== 'undefined' && rest.body instanceof FormData;

  const headers = isFormData
    ? { ...customHeaders }
    : { 'Content-Type': 'application/json', ...customHeaders };

  const config = {
    ...rest,
    headers,
  };

  if (config.body && typeof config.body === 'object' && !isFormData) {
    config.body = JSON.stringify(config.body);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  config.signal = config.signal || controller.signal;

  try {
    const res = await fetch(`${BASE_URL}${url}`, config);

    if (!res.ok) {
      const errorText = await res.text().catch(() => '');
      let message = errorText || `Request failed: ${res.status} ${res.statusText}`;
      try {
        const parsed = JSON.parse(errorText);
        if (parsed?.detail) message = parsed.detail;
      } catch {
        // Keep plain errorText
      }
      throw new Error(message);
    }

    const contentType = res.headers.get('content-type');
    if (contentType && contentType.includes('application/json')) {
      return res.json();
    }
    return res;
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('Request timed out. Agents may still be finishing in the background.');
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
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
  uploadSkillFile: (projectId, formData) =>
    request(`/projects/${projectId}/skills/upload`, { method: 'POST', body: formData }),
  deleteSkill: (id) => request(`/skills/${id}`, { method: 'DELETE' }),

  // Bugs
  getBugs: (projectId) => request(`/projects/${projectId}/bugs`),
  getBug: (id) => request(`/bugs/${id}`),
  deleteBug: (id) => request(`/bugs/${id}`, { method: 'DELETE' }),
  retestBug: (id) => request(`/bugs/${id}/retest`, { method: 'POST' }),
  raiseBugToJira: (id) => request(`/bugs/${id}/raise-jira`, { method: 'POST' }),
  updateBugStatus: (id, status) => request(`/bugs/${id}/status`, { method: 'PUT', body: { status } }),
  autoTest: (projectId, data) =>
    request(`/projects/${projectId}/bugs/auto-test`, { method: 'POST', body: data, timeoutMs: 240000 }),

  // Chat
  sendMessage: (data) => request('/chat', { method: 'POST', body: data, timeoutMs: 240000 }),

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

  // Reports & Dashboard
  getDashboardStats: () => request('/reports/dashboard'),
  getSummary: (projectId) => request(`/reports/summary/${projectId}`),

  // Evidence
  getEvidenceUrl: (id) => `${BASE_URL}/evidence/${id}/file`,
  getRawEvidenceUrl: (fileName) => `${BASE_URL}/evidence/raw/${encodeURIComponent(fileName || '')}`,
  resolveEvidenceUrl: (ev) => {
    if (!ev) return '';
    if (ev.id) return `${BASE_URL}/evidence/${ev.id}/file`;
    if (ev.file_name || ev.filename) {
      return `${BASE_URL}/evidence/raw/${encodeURIComponent(ev.file_name || ev.filename)}`;
    }
    return '';
  },
};

export default api;
