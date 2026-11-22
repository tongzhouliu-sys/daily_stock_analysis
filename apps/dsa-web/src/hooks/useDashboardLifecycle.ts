import { useEffect, useRef } from 'react';
import type { TaskInfo } from '../types/analysis';
import { useTaskStream } from './useTaskStream';

type UseDashboardLifecycleOptions = {
  loadInitialHistory: () => Promise<void>;
  refreshHistory: (silent?: boolean) => Promise<void>;
  refreshHistoryForCompletedTask?: (task: TaskInfo) => Promise<void>;
  refreshActiveTasks: () => Promise<void>;
  loadStockBar: () => Promise<void>;
  refreshStockBar: (silent?: boolean) => Promise<void>;
  loadMarketReviewHistory?: () => Promise<void>;
  refreshMarketReviewHistory?: (silent?: boolean) => Promise<void>;
  syncTaskCreated: (task: TaskInfo) => void;
  syncTaskUpdated: (task: TaskInfo) => void;
  syncTaskFailed: (task: TaskInfo) => void;
  removeTask: (taskId: string) => void;
  onDashboardDataRefresh?: () => void;
  onCompletedTaskDataRefreshStarted?: (task: TaskInfo) => void;
  onCompletedTaskDataRefreshed?: (task: TaskInfo) => void;
  enabled?: boolean;
};

export interface UseDashboardLifecycleResult {
  isConnected: boolean;
  heartbeatIntervalMs: number;
}

export function useDashboardLifecycle({
  loadInitialHistory,
  refreshHistory,
  refreshHistoryForCompletedTask,
  refreshActiveTasks,
  loadStockBar,
  refreshStockBar,
  loadMarketReviewHistory,
  refreshMarketReviewHistory,
  syncTaskCreated,
  syncTaskUpdated,
  syncTaskFailed,
  removeTask,
  onDashboardDataRefresh,
  onCompletedTaskDataRefreshStarted,
  onCompletedTaskDataRefreshed,
  enabled = true,
}: UseDashboardLifecycleOptions): UseDashboardLifecycleResult {
  const removalTimeoutsRef = useRef<number[]>([]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    void loadInitialHistory();
    void loadStockBar();
    void loadMarketReviewHistory?.();
    void refreshActiveTasks();
  }, [enabled, loadInitialHistory, loadMarketReviewHistory, loadStockBar, refreshActiveTasks]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const intervalId = window.setInterval(() => {
      // T20: hidden tabs skip the tick entirely; the visibility handler catches
      // up when the user comes back.
      if (document.hidden) {
        return;
      }
      void refreshHistory(true);
      void refreshStockBar(true);
      void refreshMarketReviewHistory?.(true);
      void refreshActiveTasks();
      onDashboardDataRefresh?.();
    }, 30_000);

    return () => window.clearInterval(intervalId);
  }, [enabled, onDashboardDataRefresh, refreshHistory, refreshMarketReviewHistory, refreshStockBar, refreshActiveTasks]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refreshHistory(true);
        void refreshStockBar(true);
        void refreshMarketReviewHistory?.(true);
        void refreshActiveTasks();
        onDashboardDataRefresh?.();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [enabled, onDashboardDataRefresh, refreshHistory, refreshMarketReviewHistory, refreshStockBar, refreshActiveTasks]);

  useEffect(() => {
    return () => {
      removalTimeoutsRef.current.forEach((timeoutId) => window.clearTimeout(timeoutId));
      removalTimeoutsRef.current = [];
    };
  }, []);

  const scheduleTaskRemoval = (taskId: string, delayMs: number) => {
    const timeoutId = window.setTimeout(() => {
      removeTask(taskId);
      removalTimeoutsRef.current = removalTimeoutsRef.current.filter((item) => item !== timeoutId);
    }, delayMs);

    removalTimeoutsRef.current.push(timeoutId);
  };

  const taskStream = useTaskStream({
    onTaskCreated: syncTaskCreated,
    onTaskStarted: syncTaskUpdated,
    onTaskProgress: syncTaskUpdated,
    onConnected: () => {
      void refreshActiveTasks();
    },
    onTaskCompleted: (task) => {
      syncTaskUpdated(task);
      onCompletedTaskDataRefreshStarted?.(task);
      const historyRefresh = refreshHistoryForCompletedTask
        ? refreshHistoryForCompletedTask(task)
        : refreshHistory(true);
      // Silent: the panel keeps its rows while the completed task's data loads.
      const stockBarRefresh = refreshStockBar(true);
      void Promise.allSettled([historyRefresh, stockBarRefresh]).then(() => {
        onCompletedTaskDataRefreshed?.(task);
      });
      void refreshMarketReviewHistory?.(true);
      scheduleTaskRemoval(task.taskId, 2_000);
    },
    onTaskFailed: (task) => {
      // Failures stay on screen: the run card and the panel are the only
      // places to retry from, so a timed removal hides the reason (H8).
      syncTaskFailed(task);
    },
    onTaskCancelRequested: syncTaskUpdated,
    onTaskCancelled: (task) => {
      syncTaskUpdated(task);
      scheduleTaskRemoval(task.taskId, 2_000);
    },
    onError: () => {
      console.warn('SSE connection disconnected, reconnecting...');
    },
    enabled,
  });

  // Tests and embedders may stub `useTaskStream`; treat a missing result as
  // "connected" so behaviour only changes when the real hook reports a drop.
  const streamConnected = taskStream?.isConnected ?? true;

  // While the stream is down, the 30 s reconciliation loop is too slow to
  // notice a finished task. Tighten it only while disconnected.
  useEffect(() => {
    if (!enabled || streamConnected) {
      return;
    }
    const intervalId = window.setInterval(() => {
      if (document.hidden) {
        return;
      }
      void refreshActiveTasks();
    }, 3_000);
    return () => window.clearInterval(intervalId);
  }, [enabled, refreshActiveTasks, streamConnected]);

  return {
    isConnected: streamConnected,
    heartbeatIntervalMs: taskStream?.heartbeatIntervalMs ?? 30_000,
  };
}

export default useDashboardLifecycle;
