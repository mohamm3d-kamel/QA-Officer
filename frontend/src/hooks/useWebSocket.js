import { useEffect, useRef } from 'react';
import useAppStore from '../store/appStore';
import toast from 'react-hot-toast';

let sharedWs = null;
let reconnectTimer = null;
let idleCloseTimer = null;
let subscriberCount = 0;
let activeProjectId = null;
const listeners = new Set();

function handleIncomingPayload(data) {
  const store = useAppStore.getState();

  listeners.forEach((cb) => {
    try {
      cb(data);
    } catch (err) {
      console.error('[WS] Listener error:', err);
    }
  });

  const targetProjectId = data.project_id ?? activeProjectId ?? store.selectedProject?.id;

  switch (data.type) {
    case 'agent_status':
      store.setAgentStatus(
        data.agent,
        data.status,
        data.message,
        data.data?.bug_id ?? data.bug_id,
        targetProjectId
      );
      if (data.status === 'running' && data.agent !== 'desk_officer') {
        store.addMessage(
          {
            id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            sender: 'agent',
            role: 'agent',
            agent: data.agent,
            content: data.message || `${data.agent} is working...`,
            timestamp: new Date().toISOString(),
            isStatus: true,
          },
          targetProjectId
        );
      }
      break;

    case 'desk_stream':
      store.upsertStreamChunk(data.stream_id, data.delta, targetProjectId);
      break;

    case 'desk_stream_end':
      store.endStreamIndicator(data.stream_id);
      break;

    case 'chat_response':
      if (data.done) {
        store.setChatLoading(false);
      } else {
        store.appendToLastMessage(data.content, targetProjectId);
      }
      break;

    case 'watcher_event':
      if (data.log) {
        store.addWatcherEvent(data.log);
      }
      break;

    case 'notification':
      store.addNotification({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        title: data.title,
        message: data.message,
        level: data.level,
        module: data.module,
        agent: data.agent,
        jira_key: data.jira_key,
        timestamp: data.timestamp || new Date().toISOString(),
      });
      if (data.level === 'error') {
        toast.error(data.message);
      } else if (data.level === 'success') {
        toast.success(data.message);
      } else {
        toast(data.message, { icon: data.level === 'warning' ? '⚠️' : 'ℹ️' });
      }
      break;

    default:
      break;
  }
}

function ensureConnected() {
  if (idleCloseTimer) {
    clearTimeout(idleCloseTimer);
    idleCloseTimer = null;
  }
  if (
    sharedWs &&
    (sharedWs.readyState === WebSocket.OPEN || sharedWs.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws`;
  const ws = new WebSocket(wsUrl);
  ws._cancelled = false;
  sharedWs = ws;

  ws.onopen = () => {
    if (ws._cancelled || subscriberCount === 0 || sharedWs !== ws) {
      ws.close();
      return;
    }
    console.log('[WS] Connected');
    useAppStore.getState().setWsConnected(true);
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onmessage = (event) => {
    if (ws._cancelled || sharedWs !== ws) return;
    try {
      const data = JSON.parse(event.data);
      handleIncomingPayload(data);
    } catch (err) {
      console.error('[WS] Parse error:', err);
    }
  };

  ws.onclose = () => {
    if (ws._cancelled) return;
    console.log('[WS] Disconnected');
    useAppStore.getState().setWsConnected(false);
    if (sharedWs === ws) {
      sharedWs = null;
    }
    if (subscriberCount > 0 && !reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (subscriberCount > 0) ensureConnected();
      }, 3000);
    }
  };

  ws.onerror = (err) => {
    if (ws._cancelled) return;
    console.error('[WS] Error:', err);
    useAppStore.getState().setWsConnected(false);
    if (ws.readyState === WebSocket.OPEN) {
      ws.close();
    }
  };
}

export default function useWebSocket(onMessage, projectId = null) {
  const isConnected = useAppStore((s) => s.wsConnected);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (projectId !== undefined && projectId !== null) {
      activeProjectId = projectId;
    }
  }, [projectId]);

  useEffect(() => {
    subscriberCount += 1;
    const listener = (data) => {
      if (onMessageRef.current) onMessageRef.current(data);
    };
    listeners.add(listener);
    ensureConnected();

    return () => {
      listeners.delete(listener);
      subscriberCount = Math.max(0, subscriberCount - 1);
      if (subscriberCount === 0) {
        if (reconnectTimer) {
          clearTimeout(reconnectTimer);
          reconnectTimer = null;
        }
        // Delay closing slightly so React StrictMode double-mount reuses the socket cleanly
        idleCloseTimer = setTimeout(() => {
          idleCloseTimer = null;
          if (subscriberCount === 0 && sharedWs) {
            const wsToClose = sharedWs;
            sharedWs = null;
            wsToClose._cancelled = true;
            if (wsToClose.readyState === WebSocket.OPEN) {
              wsToClose.close();
            }
          }
        }, 150);
      }
    };
  }, []);

  return { isConnected };
}
