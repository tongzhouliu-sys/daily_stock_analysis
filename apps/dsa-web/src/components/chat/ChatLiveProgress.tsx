import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Square } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { ElapsedTime } from '../run-status';
import ChatAnswer from './ChatAnswer';
import type { ProgressStep } from '../../stores/agentChatStore';
import {
  CHAT_RUN_THRESHOLDS,
  RUN_STAGE_LABEL_KEYS,
  deriveRunState,
  type RunState,
} from '../../utils/runStatus';
import { deriveChatPhase, summarizeProgressSteps, type ChatPhase } from '../../utils/chatProgress';

type Phase = ChatPhase;

const PHASE_ORDER: Phase[] = ['understanding', 'analyzing', 'generating'];
const PHASE_LABEL_KEYS = {
  understanding: 'chat.progress.phase.understanding',
  analyzing: 'chat.progress.phase.analyzing',
  generating: 'chat.progress.phase.generating',
} as const;

interface ChatLiveProgressProps {
  steps: ProgressStep[];
  streamingContent: string | null;
  language: 'zh' | 'en';
  turnStartedAt?: number | null;
  lastActivityAt?: number | null;
  onStop?: () => void;
}

interface RenderedStep {
  key: string;
  label: string;
  meta: string;
  tone: 'running' | 'done' | 'error';
}

function toRenderedStep(
  step: ProgressStep,
  t: ReturnType<typeof useUiLanguage>['t'],
  now = Date.now(),
): RenderedStep {
  const label = step.display_label || step.display_name || step.tool || step.stage || '';
  const failed = step.outcome === 'failed' || step.outcome === 'timeout' || step.success === false;
  if (step.type === 'tool_start') {
    const waited = step.receivedAt ? Math.max(0, Math.round((now - step.receivedAt) / 1000)) : 0;
    return {
      key: step.call_id || `tool:${step.tool || label}`,
      label,
      meta: t('chat.progress.toolRunning', { seconds: waited }),
      tone: 'running',
    };
  }
  if (step.type === 'tool_done') {
    // The step label is already rendered on the left; the trailing text only
    // carries what the label cannot: duration, record count and cache origin.
    const duration = step.duration ? numberLabel(step.duration) : '';
    const details: string[] = [];
    if (duration) details.push(duration);
    if (typeof step.detail?.record_count === 'number') {
      details.push(t('chat.progress.records', { count: step.detail.record_count }));
    }
    if (step.detail?.data_source === 'cache') details.push(t('chat.progress.cached'));
    return {
      key: step.call_id || `tool:${step.tool || label}`,
      label,
      meta: failed
        ? t(step.outcome === 'timeout' ? 'chat.progress.toolTimeout' : 'chat.progress.toolFailed')
        : details.join(' · '),
      tone: failed ? 'error' : 'done',
    };
  }
  if (step.type === 'stage_start' || step.type === 'stage_done') {
    const stageLabel = step.stage && RUN_STAGE_LABEL_KEYS[step.stage as keyof typeof RUN_STAGE_LABEL_KEYS]
      ? t(RUN_STAGE_LABEL_KEYS[step.stage as keyof typeof RUN_STAGE_LABEL_KEYS])
      : (step.stage || label);
    return {
      key: step.stage_id || `stage:${step.stage || label}`,
      label: stageLabel,
      meta: step.type === 'stage_start'
        ? t('chat.progress.waiting')
        : numberLabel(step.duration),
      tone: step.type === 'stage_start' ? 'running' : (failed ? 'error' : 'done'),
    };
  }
  return {
    key: step.type,
    label,
    meta: step.message || '',
    tone: 'running',
  };
}

function numberLabel(value?: number): string {
  if (!value || !Number.isFinite(value)) return '';
  return `${Math.round(value * 10) / 10}s`;
}

/**
 * Live "in progress" panel for one chat turn (T40, PR-B).
 *
 * Shows a three-phase strip, a merged step timeline, the elapsed time and a
 * stop action. Styling lives in `research.css` (`.chat-live-*`).
 */
function ChatLiveProgress({
  steps,
  streamingContent,
  turnStartedAt = null,
  lastActivityAt = null,
  onStop,
}: ChatLiveProgressProps) {
  const { t } = useUiLanguage();
  const [expanded, setExpanded] = useState(false);
  const previewRef = useRef<HTMLDivElement>(null);
  const phase = deriveChatPhase(steps);
  const state: RunState = deriveRunState({
    status: 'processing',
    lastActivityAt,
    progressUpdatedAt: null,
    startedAt: turnStartedAt,
    thresholds: CHAT_RUN_THRESHOLDS,
  });
  const recovering = steps.some((step) => step.type === 'recovering');

  const renderedSteps = useMemo(() => {
    const summarized = summarizeProgressSteps(steps)
      .map((item) => item.step)
      .filter((step) => step.type !== 'thinking');
    return summarized.map((step) => toRenderedStep(step, t));
  }, [steps, t]);

  const visibleSteps = expanded ? renderedSteps : renderedSteps.slice(-6);
  const hiddenCount = Math.max(0, renderedSteps.length - visibleSteps.length);
  const canToggleSteps = expanded || hiddenCount > 0;

  // The streaming preview is height-capped so a long answer cannot push the
  // progress panel off-screen. Keep its own scroll pinned to the newest text;
  // otherwise the reader only ever sees the first lines until the turn ends.
  useEffect(() => {
    const preview = previewRef.current;
    if (preview) preview.scrollTop = preview.scrollHeight;
  }, [streamingContent]);

  const headText = recovering
    ? t('chat.progress.recoveringHint')
    : state === 'disconnected'
      ? t('runStatus.disconnected')
      : state === 'stalled'
        ? t('runStatus.stallHint')
        : state === 'slow'
          ? t('runStatus.slowHint')
          : t(PHASE_LABEL_KEYS[phase]);

  const subText = recovering
    ? t('chat.progress.recovering')
    : phase === 'understanding'
      ? t('chat.progress.understandingStep')
      : phase === 'generating'
        ? t('chat.progress.generating')
        : t('chat.progress.analyzingData');

  return (
    <div className="chat-msg chat-msg-ai">
      <div
        role="status"
        aria-live="polite"
        className="chat-live-progress"
        data-testid="chat-live-progress"
        data-state={state}
        data-phase={phase}
      >
        <div className="chat-live-head">
          <span className="chat-live-spinner" aria-hidden="true" />
          <span className="chat-live-phase">{headText}</span>
          <span className="chat-live-sub">{subText}</span>
          <ElapsedTime startedAt={turnStartedAt} className="chat-live-elapsed" />
          {onStop ? (
            <button
              type="button"
              className="chat-live-stop"
              onClick={onStop}
              aria-label={t('chat.progress.stopTurn')}
            >
              <Square size={12} aria-hidden="true" />
              <span>{t('chat.progress.stop')}</span>
            </button>
          ) : null}
        </div>

        <ol className="chat-live-phases" aria-hidden="true">
          {PHASE_ORDER.map((item) => {
            const itemIndex = PHASE_ORDER.indexOf(item);
            const activeIndex = PHASE_ORDER.indexOf(phase);
            return (
              <li
                key={item}
                className={cn(
                  'chat-live-phase-item',
                  itemIndex < activeIndex && 'is-done',
                  itemIndex === activeIndex && 'is-current',
                )}
              >
                <span className="chat-live-phase-dot" />
                <span>{t(PHASE_LABEL_KEYS[item])}</span>
              </li>
            );
          })}
        </ol>

        {renderedSteps.length > 0 ? (
          <div className="chat-live-details">
            {canToggleSteps ? (
              <button
                type="button"
                className="chat-live-more"
                onClick={() => setExpanded((value) => !value)}
                aria-expanded={expanded}
              >
                {expanded
                  ? t('chat.progress.collapseEarlier')
                  : t('chat.progress.expandEarlier', { count: hiddenCount })}
                <ChevronDown
                  size={12}
                  aria-hidden="true"
                  className={cn('chat-live-more-icon', expanded && 'is-flipped')}
                />
              </button>
            ) : null}
            <div className={cn('chat-live-steps', expanded && 'is-expanded')}>
              {visibleSteps.map((step) => (
                <div
                  key={step.key}
                  className={cn(
                    'chat-live-step',
                    step.tone === 'done' && 'is-done',
                    step.tone === 'error' && 'is-error',
                  )}
                >
                  <span className="chat-live-step-dot" aria-hidden="true" />
                  <span className="chat-live-step-label">{step.label}</span>
                  <span className="chat-live-step-meta">{step.meta}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {streamingContent ? (
          <div
            ref={previewRef}
            className="chat-prose chat-streaming-preview"
            aria-live="polite"
          >
            <ChatAnswer content={streamingContent} isStreaming />
            <span className="chat-streaming-caret" aria-hidden="true" />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default memo(ChatLiveProgress);
