import { useMemo } from 'react';
import { ArrowRight, Workflow } from 'lucide-react';
import { Button } from '../common';
import { ElapsedTime, RunStatusIcon, StageProgress } from '../run-status';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { TaskInfo } from '../../types/analysis';
import {
  TASK_RUN_THRESHOLDS,
  deriveRunState,
  parseTimestamp,
} from '../../utils/runStatus';

interface AnalysisRunCardProps {
  /** The task this page is tracking (the most recent submission). */
  task: TaskInfo;
  /** All active tasks, used for the "N more running" affordance. */
  activeTasks: TaskInfo[];
  isConnected: boolean;
  /** How many queued tasks were created before this one. */
  queuedPosition?: number;
  onCancel: (taskId: string) => void;
  onRetry: (task: TaskInfo) => void;
  onOpenRunFlow: (task: TaskInfo) => void;
  onViewReport: (task: TaskInfo) => void;
  onDismiss: () => void;
  onOpenTasksPanel: () => void;
}

/**
 * Run feedback for the home page.
 *
 * Sits at the top of the reading pane (not in the sidebar) so a submission is
 * visible on phones too, and keeps failures on screen instead of flashing for
 * two seconds.
 */
export function AnalysisRunCard({
  task,
  activeTasks,
  isConnected,
  queuedPosition = 0,
  onCancel,
  onRetry,
  onOpenRunFlow,
  onViewReport,
  onDismiss,
  onOpenTasksPanel,
}: AnalysisRunCardProps) {
  const { t, language } = useUiLanguage();
  const state = deriveRunState({
    status: task.status,
    connected: isConnected,
    progressUpdatedAt: task.progressUpdatedAt,
    startedAt: parseTimestamp(task.startedAt ?? task.createdAt),
    thresholds: TASK_RUN_THRESHOLDS,
  });
  const isMarketReview = task.reportType === 'market_review';
  const othersRunning = Math.max(
    0,
    activeTasks.filter((item) => item.taskId !== task.taskId).length,
  );
  const canCancel = task.status === 'pending' || task.status === 'processing';

  const title = isMarketReview ? t('runCard.marketReviewTitle') : t('runCard.title');

  const detailLine = useMemo(() => {
    if (state === 'disconnected') return t('runStatus.disconnected');
    if (state === 'stalled') return t('runStatus.stallHint');
    if (state === 'slow') return t('runStatus.slowHint');
    if (state === 'queued') {
      return queuedPosition > 0
        ? t('runStatus.queuedPosition', { count: queuedPosition })
        : t('runStatus.state.queued');
    }
    if (state === 'done') return t('runCard.completed');
    if (state === 'cancelled') {
      return task.status === 'cancel_requested'
        ? t('runCard.cancelRequested')
        : t('runStatus.state.cancelled');
    }
    if (state === 'failed') {
      return task.error?.trim() || t('runCard.failedFallback');
    }
    if (task.status === 'cancel_requested') return t('runCard.cancelRequested');
    // Chinese UI shows the server step text; English falls back to the stage
    // strip because server messages are Chinese only.
    if (language === 'en') return '';
    return task.message?.trim() || '';
  }, [language, queuedPosition, state, t, task.error, task.message, task.status]);

  return (
    <section
      className={`run-card is-${state}`}
      data-testid="analysis-run-card"
      data-state={state}
      aria-label={title}
      role="status"
      aria-live="polite"
    >
      <div className="run-card-head">
        <RunStatusIcon state={state} />
        <span className="run-card-title">{title}</span>
        <span className="run-card-stock">
          {task.stockName || task.stockCode}
          <span className="run-card-code">{task.stockCode}</span>
        </span>
        {isMarketReview && task.region ? (
          <span className="run-card-region">{t('runCard.region', { region: task.region })}</span>
        ) : null}
        <ElapsedTime
          className="run-card-elapsed"
          startedAt={task.startedAt ?? task.createdAt}
        />
      </div>

      {task.stage || isMarketReview ? (
        <>
          <div className="run-card-stage-full">
            <StageProgress stage={task.stage} indeterminate={isMarketReview} />
          </div>
          <div className="run-card-stage-compact">
            <StageProgress stage={task.stage} indeterminate={isMarketReview} compact />
          </div>
        </>
      ) : null}

      {detailLine ? (
        <p className="run-card-detail" data-testid="analysis-run-card-detail">{detailLine}</p>
      ) : null}

      <div className="run-card-actions">
        {canCancel ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => onCancel(task.taskId)}
            className="run-card-cancel"
            disabled={task.status === 'cancel_requested'}
          >
            {t('runCard.cancel')}
          </Button>
        ) : null}
        {state === 'failed' ? (
          <>
            <Button type="button" variant="primary" size="sm" onClick={() => onRetry(task)}>
              {t('runCard.retry')}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={onDismiss}>
              {t('runCard.close')}
            </Button>
          </>
        ) : null}
        {state === 'done' ? (
          <>
            <Button type="button" variant="primary" size="sm" onClick={() => onViewReport(task)}>
              {t('runCard.viewReport')}
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={onDismiss}>
              {t('runCard.close')}
            </Button>
          </>
        ) : null}
        {state === 'cancelled' ? (
          <Button type="button" variant="ghost" size="sm" onClick={onDismiss}>
            {t('runCard.close')}
          </Button>
        ) : null}
        {!isMarketReview ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onOpenRunFlow(task)}
            className="run-card-flow"
          >
            <Workflow className="h-3.5 w-3.5" aria-hidden="true" />
            {t('runCard.openRunFlow')}
          </Button>
        ) : null}
        {othersRunning > 0 ? (
          <button
            type="button"
            className="run-card-more"
            onClick={onOpenTasksPanel}
          >
            {t('runCard.othersRunning', { count: othersRunning })}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </section>
  );
}

export default AnalysisRunCard;
