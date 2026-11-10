import { useEffect, useRef, useCallback, useState } from 'react';
import useAppStore from '../store/appStore';
import toast from 'react-hot-toast';

export default function useWebSocket(onMessage) {
  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const [isConnected, setIsConnected] = useState(false);
  const setAgentStatus = useAppStore((s) => s.setAgentStatus);
  const addMessage = useAppStore((s) => s.addMessage);
  const appendToLastMessage = useAppStore((s) => s.appendToLastMessage);
  const addNotification = useAppStore((s) => s.addNotification);
  const setChatLoading = useAppStore((s) => s.setChatLoading);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[WS] Connected');
      setIsConnected(true);
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (onMessageRef.current) {
          onMessageRef.current(data);
        }

        switch (data.type) {
          case 'agent_status':
            setAgentStatus(data.agent, data.status, data.message, data.data?.bug_id ?? data.bug_id);
            if (data.status === 'running') {
              addMessage({
                id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
                role: 'agent',
                agent: data.agent,
                content: data.message || `${data.agent} is working...`,
                timestamp: new Date().toISOString(),
                isStatus: true,
              });
            }
            break;

          case 'chat_response':
            if (data.done) {
              setChatLoading(false);
            } else {
              appendToLastMessage(data.content);
            }
            break;

          case 'notification':
            addNotification({
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
              title: data.title,
              message: data.message,
              level: data.level,
              timestamp: new Date().toISOString(),
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
      console.log('[WS] Disconnected, reconnecting in 3s...');
      setIsConnected(false);
      reconnectTimer.current = setTimeout(connect, 3000);
    };

    ws.onerror = (err) => {
      console.error('[WS] Error:', err);
      setIsConnected(false);
      ws.close();
    };
  }, [setAgentStatus, addMessage, appendToLastMessage, addNotification, setChatLoading]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { wsRef, isConnected };
}
