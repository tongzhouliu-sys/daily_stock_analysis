import { useEffect, useRef, useCallback, useState, type MutableRefObject } from 'react';
import { analysisApi } from '../api/analysis';
import { toCamelCase } from '../api/utils';
import type { TaskInfo } from '../types/analysis';
import type { RunFlowEvent } from '../types/runFlow';

/**
 * SSE event types.
 */
export type SSEEventType =
  | 'connected'
  | 'task_created'
  | 'task_started'
  | 'task_progress'
  | 'task_completed'
  | 'task_failed'
  | 'task_cancel_requested'
  | 'task_cancelled'
  | 'heartbeat';

/**
 * SSE event payload.
 */
export interface SSEEvent {
  type: SSEEventType;
  task?: TaskInfo;
  flowEvent?: RunFlowEvent;
  timestamp?: string;
}

/**
 * SSE hook options.
 */
export interface UseTaskStreamOptions {
  /** Task created callback */
  onTaskCreated?: (task: TaskInfo) => void;
  /** Task started callback */
  onTaskStarted?: (task: TaskInfo) => void;
  /** Task completed callback */
  onTaskCompleted?: (task: TaskInfo) => void;
  /** Task progress callback */
  onTaskProgress?: (task: TaskInfo) => void;
  /** Task failed callback */
  onTaskFailed?: (task: TaskInfo) => void;
  /** A running task was asked to cancel and will stop at its next checkpoint */
  onTaskCancelRequested?: (task: TaskInfo) => void;
  /** Task cancelled callback (terminal) */
  onTaskCancelled?: (task: TaskInfo) => void;
  /** Incremental run-flow event callback carried by task_progress */
  onTaskFlowEvent?: (task: TaskInfo, event: RunFlowEvent) => void;
  /** Connected callback */
  onConnected?: () => void;
  /** Connection error callback */
  onError?: (error: Event) => void;
  /** Whether to reconnect automatically */
  autoReconnect?: boolean;
  /** Reconnect delay in milliseconds */
  reconnectDelay?: number;
  /** Whether the hook is enabled */
  enabled?: boolean;
}

/**
 * SSE hook result.
 */
export interface UseTaskStreamResult {
  /** Whether the stream is connected */
  isConnected: boolean;
  /** Reconnect manually */
  reconnect: () => void;
  /** Disconnect manually */
  disconnect: () => void;
  /** Server-reported heartbeat interval, used by silence watchdogs */
  heartbeatIntervalMs: number;
}

type TaskStreamCallbacks = Pick<
  UseTaskStreamOptions,
  | 'onTaskCreated'
  | 'onTaskStarted'
  | 'onTaskCompleted'
  | 'onTaskProgress'
  | 'onTaskFailed'
  | 'onTaskCancelRequested'
  | 'onTaskCancelled'
  | 'onTaskFlowEvent'
  | 'onConnected'
  | 'onError'
>;

type ParsedTaskStreamPayload = {
  task: TaskInfo;
  flowEvent?: RunFlowEvent;
};

type TaskStreamSubscriber = {
  callbacksRef: MutableRefObject<TaskStreamCallbacks>;
  setIsConnected: (value: boolean) => void;
  setHeartbeatIntervalMs: (value: number) => void;
  autoReconnect: boolean;
  reconnectDelay: number;
};

/** Server sends a heartbeat every 30 s; the value arrives on `connected`. */
const DEFAULT_HEARTBEAT_INTERVAL_MS = 30_000;
/** Reconnect when no event (heartbeat included) arrived for this many intervals. */
const SILENCE_INTERVAL_MULTIPLIER = 2.5;
const WATCHDOG_TICK_MS = 5_000;

let sharedEventSource: EventSource | null = null;
let sharedReconnectTimeout: ReturnType<typeof setTimeout> | null = null;
let sharedWatchdogTimer: ReturnType<typeof setInterval> | null = null;
let sharedConnected = false;
let sharedHeartbeatIntervalMs = DEFAULT_HEARTBEAT_INTERVAL_MS;
let sharedLastActivityAt = 0;
let nextSubscriberId = 1;
const subscribers = new Map<number, TaskStreamSubscriber>();

// Convert snake_case payloads into camelCase TaskInfo objects.
const toTaskInfo = (data: Record<string, unknown>): TaskInfo => {
  const task: TaskInfo = {
    taskId: data.task_id as string,
    stockCode: data.stock_code as string,
    stockName: data.stock_name as string | undefined,
    status: data.status as TaskInfo['status'],
    progress: data.progress as number,
    message: data.message as string | undefined,
    reportType: data.report_type as string,
    createdAt: data.created_at as string,
    startedAt: data.started_at as string | undefined,
    completedAt: data.completed_at as string | undefined,
    error: data.error as string | undefined,
    originalQuery: data.original_query as string | undefined,
    selectionSource: data.selection_source as string | undefined,
    analysisPhase: data.analysis_phase as TaskInfo['analysisPhase'],
    skills: Array.isArray(data.skills) ? data.skills.map(String) : undefined,
    assetType: data.asset_type as TaskInfo['assetType'] | undefined,
  };

  if (typeof data.trace_id === 'string' && data.trace_id.trim()) {
    task.traceId = data.trace_id;
  }
  if (typeof data.region === 'string' && data.region.trim()) {
    task.region = data.region;
  }
  if (typeof data.stage === 'string' && data.stage.trim()) {
    task.stage = data.stage as TaskInfo['stage'];
  }
  if (typeof data.progress_updated_at === 'string' && data.progress_updated_at.trim()) {
    task.progressUpdatedAt = data.progress_updated_at;
  }

  return task;
};

const parseEventData = (eventData: string): ParsedTaskStreamPayload | null => {
  markStreamActivity();
  try {
    const data = JSON.parse(eventData);
    const task = toTaskInfo(data);
    const flowEvent = data.flow_event
      ? toCamelCase<RunFlowEvent>(data.flow_event)
      : undefined;
    return { task, flowEvent };
  } catch (e) {
    console.error('Failed to parse SSE event data:', e);
    return null;
  }
};

const notifyConnectionState = (connected: boolean) => {
  sharedConnected = connected;
  subscribers.forEach((subscriber) => subscriber.setIsConnected(connected));
};

const notifyHeartbeatInterval = () => {
  subscribers.forEach((subscriber) => (
    subscriber.setHeartbeatIntervalMs(sharedHeartbeatIntervalMs)
  ));
};

/** Any server byte (business event or heartbeat) proves the stream is alive. */
const markStreamActivity = () => {
  sharedLastActivityAt = Date.now();
};

const stopSharedWatchdog = () => {
  if (sharedWatchdogTimer !== null) {
    clearInterval(sharedWatchdogTimer);
    sharedWatchdogTimer = null;
  }
};

const startSharedWatchdog = () => {
  stopSharedWatchdog();
  sharedWatchdogTimer = setInterval(() => {
    if (!sharedEventSource || subscribers.size === 0) {
      return;
    }
    const silenceLimit = sharedHeartbeatIntervalMs * SILENCE_INTERVAL_MULTIPLIER;
    if (Date.now() - sharedLastActivityAt <= silenceLimit) {
      return;
    }
    // A half-open connection never fires `onerror`; reconnect instead of
    // leaving the panel frozen behind a dead socket (H7).
    console.warn('[task-stream] no heartbeat for too long, reconnecting');
    closeSharedConnection();
    connectSharedStream();
  }, WATCHDOG_TICK_MS);
};

const forEachSubscriber = (notify: (callbacks: TaskStreamCallbacks) => void) => {
  subscribers.forEach((subscriber) => notify(subscriber.callbacksRef.current));
};

const clearSharedReconnect = () => {
  if (sharedReconnectTimeout) {
    clearTimeout(sharedReconnectTimeout);
    sharedReconnectTimeout = null;
  }
};

const closeSharedConnection = () => {
  clearSharedReconnect();
  stopSharedWatchdog();
  if (sharedEventSource) {
    sharedEventSource.close();
    sharedEventSource = null;
  }
  notifyConnectionState(false);
};

const scheduleSharedReconnect = () => {
  if (sharedReconnectTimeout || subscribers.size === 0) {
    return;
  }
  const reconnectDelays = Array.from(subscribers.values())
    .filter((subscriber) => subscriber.autoReconnect)
    .map((subscriber) => subscriber.reconnectDelay);
  if (reconnectDelays.length === 0) {
    return;
  }
  const reconnectDelay = Math.min(...reconnectDelays);
  sharedReconnectTimeout = setTimeout(() => {
    sharedReconnectTimeout = null;
    connectSharedStream();
  }, reconnectDelay);
};

function connectSharedStream() {
  if (sharedEventSource || subscribers.size === 0) {
    return;
  }

  if (typeof window.EventSource !== 'function') {
    notifyConnectionState(false);
    return;
  }

  const url = analysisApi.getTaskStreamUrl();
  const eventSource = new window.EventSource(url, { withCredentials: true });
  sharedEventSource = eventSource;
  sharedLastActivityAt = Date.now();
  startSharedWatchdog();

  eventSource.addEventListener('connected', (e) => {
    markStreamActivity();
    try {
      const payload = JSON.parse((e as MessageEvent<string>).data) as {
        heartbeat_interval_s?: number;
      };
      if (typeof payload?.heartbeat_interval_s === 'number' && payload.heartbeat_interval_s > 0) {
        sharedHeartbeatIntervalMs = payload.heartbeat_interval_s * 1000;
        notifyHeartbeatInterval();
      }
    } catch {
      // Legacy servers send a plain message; keep the default interval.
    }
    notifyConnectionState(true);
    forEachSubscriber((callbacks) => callbacks.onConnected?.());
  });

  eventSource.addEventListener('task_created', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => callbacks.onTaskCreated?.(payload.task));
    }
  });

  eventSource.addEventListener('task_started', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => callbacks.onTaskStarted?.(payload.task));
    }
  });

  eventSource.addEventListener('task_progress', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => {
        callbacks.onTaskProgress?.(payload.task);
        if (payload.flowEvent) {
          callbacks.onTaskFlowEvent?.(payload.task, payload.flowEvent);
        }
      });
    }
  });

  eventSource.addEventListener('task_completed', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => callbacks.onTaskCompleted?.(payload.task));
    }
  });

  eventSource.addEventListener('task_failed', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => callbacks.onTaskFailed?.(payload.task));
    }
  });

  eventSource.addEventListener('task_cancel_requested', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => callbacks.onTaskCancelRequested?.(payload.task));
    }
  });

  eventSource.addEventListener('task_cancelled', (e) => {
    const payload = parseEventData((e as MessageEvent<string>).data);
    if (payload) {
      forEachSubscriber((callbacks) => callbacks.onTaskCancelled?.(payload.task));
    }
  });

  eventSource.addEventListener('heartbeat', () => {
    markStreamActivity();
  });

  eventSource.onerror = (error) => {
    stopSharedWatchdog();
    notifyConnectionState(false);
    forEachSubscriber((callbacks) => callbacks.onError?.(error));
    if (sharedEventSource === eventSource) {
      eventSource.close();
      sharedEventSource = null;
    }
    scheduleSharedReconnect();
  };
}

const reconnectSharedStream = () => {
  closeSharedConnection();
  connectSharedStream();
};

/**
 * Task-stream SSE hook for realtime task status updates.
 */
export function useTaskStream(options: UseTaskStreamOptions = {}): UseTaskStreamResult {
  const {
    onTaskCreated,
    onTaskStarted,
    onTaskCompleted,
    onTaskProgress,
    onTaskFailed,
    onTaskCancelRequested,
    onTaskCancelled,
    onTaskFlowEvent,
    onConnected,
    onError,
    autoReconnect = true,
    reconnectDelay = 3000,
    enabled = true,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const [heartbeatIntervalMs, setHeartbeatIntervalMs] = useState(
    DEFAULT_HEARTBEAT_INTERVAL_MS,
  );
  const subscriberIdRef = useRef<number | null>(null);
  const connectTimerRef = useRef<number | null>(null);

  // Store callbacks in a ref to avoid reconnecting on every render.
  const callbacksRef = useRef<TaskStreamCallbacks>({
    onTaskCreated,
    onTaskStarted,
    onTaskCompleted,
    onTaskProgress,
    onTaskFailed,
    onTaskCancelRequested,
    onTaskCancelled,
    onTaskFlowEvent,
    onConnected,
    onError,
  });

  // Keep the latest callbacks available to the active SSE handlers.
  useEffect(() => {
    callbacksRef.current = {
      onTaskCreated,
      onTaskStarted,
      onTaskCompleted,
      onTaskProgress,
      onTaskFailed,
      onTaskCancelRequested,
      onTaskCancelled,
      onTaskFlowEvent,
      onConnected,
      onError,
    };
  });

  // Disconnect and defer the state update to avoid nested renders.
  const disconnect = useCallback(() => {
    if (connectTimerRef.current) {
      window.clearTimeout(connectTimerRef.current);
      connectTimerRef.current = null;
    }
    if (subscriberIdRef.current !== null) {
      subscribers.delete(subscriberIdRef.current);
      subscriberIdRef.current = null;
    }
    if (subscribers.size === 0) {
      closeSharedConnection();
    }
    queueMicrotask(() => setIsConnected(false));
  }, []);

  // Reconnect
  const reconnect = useCallback(() => {
    if (subscriberIdRef.current === null) {
      const subscriberId = nextSubscriberId++;
      subscriberIdRef.current = subscriberId;
      subscribers.set(subscriberId, {
        callbacksRef,
        setIsConnected,
        setHeartbeatIntervalMs,
        autoReconnect,
        reconnectDelay,
      });
    }
    reconnectSharedStream();
  }, [autoReconnect, reconnectDelay]);

  // Connect or disconnect when the hook is enabled or disabled.
  useEffect(() => {
    if (enabled) {
      const subscriberId = nextSubscriberId++;
      subscriberIdRef.current = subscriberId;
      subscribers.set(subscriberId, {
        callbacksRef,
        setIsConnected,
        setHeartbeatIntervalMs,
        autoReconnect,
        reconnectDelay,
      });
      setIsConnected(sharedConnected);
      setHeartbeatIntervalMs(sharedHeartbeatIntervalMs);
      connectTimerRef.current = window.setTimeout(() => {
        connectTimerRef.current = null;
        connectSharedStream();
      }, 0);
      return () => {
        disconnect();
      };
    }

    disconnect();
    return () => {
      disconnect();
    };
  }, [autoReconnect, disconnect, enabled, reconnectDelay]);

  return {
    isConnected,
    reconnect,
    disconnect,
    heartbeatIntervalMs,
  };
}

export default useTaskStream;
