import { cn } from '../../utils/cn';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import {
  RUN_STAGE_LABEL_KEYS,
  RUN_STAGE_ORDER,
  activeStageIndex,
} from '../../utils/runStatus';

interface StageProgressProps {
  /** Server-reported stage (`queued` … `done`). */
  stage?: string | null;
  /** No stage data available (background tasks): render an indeterminate bar. */
  indeterminate?: boolean;
  /** Narrow screens: show the current stage name and "step N / 5" only. */
  compact?: boolean;
  className?: string;
}

/**
 * Five-step analysis strip driven by the server `stage` field.
 *
 * The client never derives stages from a percentage: a percentage alone
 * cannot tell whether the pipeline is fetching news or already writing.
 */
export function StageProgress({ stage, indeterminate = false, compact = false, className }: StageProgressProps) {
  const { t } = useUiLanguage();
  const activeIndex = activeStageIndex(stage);
  const total = RUN_STAGE_ORDER.length;
  const currentIndex = Math.min(Math.max(activeIndex, 0), total - 1);

  if (indeterminate) {
    return (
      <div className={cn('run-stage run-stage-indeterminate', className)} data-testid="run-stage-indeterminate">
        <span className="run-stage-indeterminate-bar" aria-hidden="true" />
      </div>
    );
  }

  if (compact) {
    return (
      <div className={cn('run-stage-compact', className)} data-testid="run-stage-compact">
        <span className="run-stage-compact-label">
          {t(RUN_STAGE_LABEL_KEYS[RUN_STAGE_ORDER[currentIndex]])}
        </span>
        <span className="run-stage-compact-count">
          {t('runStatus.stepOf', { current: currentIndex + 1, total })}
        </span>
      </div>
    );
  }

  return (
    <ol className={cn('run-stage', className)} aria-label={t('runStatus.state.running')}>
      {RUN_STAGE_ORDER.map((key, index) => {
        const done = activeIndex > index;
        const current = activeIndex === index;
        return (
          <li
            key={key}
            className={cn('run-stage-item', done && 'is-done', current && 'is-current')}
            aria-current={current ? 'step' : undefined}
          >
            <span className="run-stage-dot" aria-hidden="true" />
            <span className="run-stage-label">{t(RUN_STAGE_LABEL_KEYS[key])}</span>
          </li>
        );
      })}
    </ol>
  );
}

export default StageProgress;
