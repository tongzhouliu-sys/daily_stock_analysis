import { useId } from 'react';
import { cn } from '../../utils/cn';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import useElapsedSeconds from '../../hooks/useElapsedSeconds';
import { formatElapsed } from '../../utils/runStatus';

interface ElapsedTimeProps {
  startedAt?: string | number | null;
  className?: string;
}

/**
 * mm:ss timer for a running task or chat turn.
 *
 * The ticking text is hidden from assistive tech; a second, slow-updating
 * value is exposed instead so screen readers are not woken every second.
 */
export function ElapsedTime({ startedAt, className }: ElapsedTimeProps) {
  const { t } = useUiLanguage();
  const seconds = useElapsedSeconds(startedAt);
  const label = t('runStatus.elapsed', { time: formatElapsed(seconds) });
  const id = useId();
  // Quarter-minute granularity is enough for a screen reader announcement.
  const quietLabel = t('runStatus.elapsed', { time: formatElapsed(seconds - (seconds % 30)) });

  return (
    <span className={cn('tabular-nums text-[11px] text-muted-text', className)} data-testid="run-elapsed">
      <span aria-hidden="true">{label}</span>
      <span className="sr-only" id={id} aria-live="off">{quietLabel}</span>
    </span>
  );
}

export default ElapsedTime;
