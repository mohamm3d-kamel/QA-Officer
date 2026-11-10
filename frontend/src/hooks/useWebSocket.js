import { useEffect, useRef, useCallback, useState } from 'react';
import useAppStore from '../store/appStore';
import toast from 'react-hot-toast';

export default function useWebSocket(onMessage, projectId = null) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const shouldReconnectRef = useRef(true);
  const onMessageRef = useRef(onMessage);
  const projectIdRef = useRef(projectId);
  onMessageRef.current = onMessage;
  projectIdRef.current = projectId;

  const [isConnected, setIsConnected] = useState(false);
  const setAgentStatus = useAppStore((s) => s.setAgentStatus);
  const addMessage = useAppStore((s) => s.addMessage);
  const appendToLastMessage = useAppStore((s) => s.appendToLastMessage);
  const addNotification = useAppStore((s) => s.addNotification);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  const connect = useCallback(() => {
    if (!shouldReconnectRef.current) return;
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    ws._cancelled = false;
    wsRef.current = ws;

    ws.onopen = () => {
      if (ws._cancelled || !shouldReconnectRef.current || wsRef.current !== ws) {
        ws.close();
        return;
      }
      setIsConnected(true);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    ws.onmessage = (event) => {
      if (ws._cancelled || wsRef.current !== ws) return;
      try {
        const data = JSON.parse(event.data);
        if (onMessageRef.current) {
          onMessageRef.current(data);
        }

        const targetProjectId = data.project_id ?? projectIdRef.current;

        switch (data.type) {
          case 'agent_status':
            setAgentStatus(
              data.agent,
              data.status,
              data.message,
              data.data?.bug_id ?? data.bug_id,
              targetProjectId
            );
            if (data.status === 'running') {
              addMessage(
                {
                  id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
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

          case 'chat_response':
            if (data.done) {
              setChatLoading(false);
            } else {
              appendToLastMessage(data.content, targetProjectId);
            }
            break;

          case 'notification':
            addNotification({
              title: data.title,
              message: data.message,
              level: data.level,
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
      } catch (err) {
        console.error('[WS] Parse error:', err);
      }
    };

    ws.onclose = () => {
      if (ws._cancelled) return;
      setIsConnected(false);
      if (wsRef.current === ws) wsRef.current = null;
      if (shouldReconnectRef.current) {
        reconnectTimer.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => {
      if (ws._cancelled) return;
      setIsConnected(false);
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [setAgentStatus, addMessage, appendToLastMessage, addNotification, setChatLoading]);

  useEffect(() => {
    shouldReconnectRef.current = true;
    connect();
    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      const currentWs = wsRef.current;
      wsRef.current = null;
      if (currentWs) {
        currentWs._cancelled = true;
        if (currentWs.readyState === WebSocket.OPEN) {
          currentWs.close();
        }
      }
    };
  }, [connect]);

  return { wsRef, isConnected };
}
