import type React from 'react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { HistoryItem } from '../../types/analysis';
import { getDecisionActionTone } from '../../utils/decisionAction';
import { formatDateTime } from '../../utils/format';
import { Badge } from '../common';
import {
  formatChangePct,
  formatModelName,
  formatNumber,
  getPriceChangeStyle,
  isPresent,
} from './historyFormat';

const PANEL_MAX_WIDTH = 660;
const VIEWPORT_MARGIN = 12;
const PANEL_GAP = 10;
const OPEN_DELAY = 110;
const CLOSE_DELAY = 160;

interface PanelPlacement {
  top: number;
  left: number;
  width: number;
}

interface AdviceDetailPopoverProps {
  item: HistoryItem;
  adviceText: string;
}

const PanelSection: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section className="mb-3 break-inside-avoid rounded-xl border border-border/55 bg-background/40 px-3 py-2.5 last:mb-0">
    <p className="text-[11px] font-medium tracking-[0.16em] text-muted-text uppercase">{title}</p>
    <div className="mt-1.5 text-sm leading-6 text-foreground">{children}</div>
  </section>
);

const PanelMetric: React.FC<{ label: string; value: React.ReactNode; style?: React.CSSProperties }> = ({
  label,
  value,
  style,
}) => (
  <div>
    <dt className="text-[11px] text-muted-text">{label}</dt>
    <dd className="mt-0.5 font-mono text-sm font-semibold text-foreground" style={style}>
      {value}
    </dd>
  </div>
);

/**
 * Truncated advice badge that expands the complete analysis conclusion into a
 * masonry detail panel on hover or keyboard focus.
 */
export const AdviceDetailPopover: React.FC<AdviceDetailPopoverProps> = ({ item, adviceText }) => {
  const { t } = useUiLanguage();
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<PanelPlacement>({ top: 0, left: 0, width: PANEL_MAX_WIDTH });
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const openTimer = useRef<number | null>(null);
  const closeTimer = useRef<number | null>(null);

  const clearTimers = useCallback(() => {
    if (openTimer.current !== null) {
      window.clearTimeout(openTimer.current);
      openTimer.current = null;
    }
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const updatePlacement = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) {
      return;
    }

    const rect = trigger.getBoundingClientRect();
    const width = Math.min(PANEL_MAX_WIDTH, window.innerWidth - VIEWPORT_MARGIN * 2);
    const height = panelRef.current?.offsetHeight ?? 0;
    const left = Math.max(
      VIEWPORT_MARGIN,
      Math.min(rect.left, window.innerWidth - width - VIEWPORT_MARGIN),
    );

    let top = rect.bottom + PANEL_GAP;
    if (height && top + height > window.innerHeight - VIEWPORT_MARGIN) {
      const above = rect.top - height - PANEL_GAP;
      top = above >= VIEWPORT_MARGIN ? above : Math.max(VIEWPORT_MARGIN, window.innerHeight - height - VIEWPORT_MARGIN);
    }

    setPlacement({ top, left, width });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }
    updatePlacement();
  }, [open, updatePlacement, item.id]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleViewportChange = () => updatePlacement();
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);

    const panel = panelRef.current;
    const observer =
      panel && typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(() => updatePlacement())
        : null;
    observer?.observe(panel as HTMLElement);

    return () => {
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
      observer?.disconnect();
    };
  }, [open, updatePlacement]);

  useEffect(() => clearTimers, [clearTimers]);

  const scheduleOpen = useCallback(() => {
    clearTimers();
    openTimer.current = window.setTimeout(() => setOpen(true), OPEN_DELAY);
  }, [clearTimers]);

  const scheduleClose = useCallback(() => {
    clearTimers();
    closeTimer.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY);
  }, [clearTimers]);

  const cancelClose = useCallback(() => {
    clearTimers();
  }, [clearTimers]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && open) {
      event.stopPropagation();
      clearTimers();
      setOpen(false);
    }
  };

  const operationAdvice = item.operationAdvice?.trim();
  const trendPrediction = item.trendPrediction?.trim();
  const analysisSummary = item.analysisSummary?.trim();
  const modelUsed = item.modelUsed?.trim();
  const hasMetrics =
    isPresent(item.currentPrice) ||
    isPresent(item.changePct) ||
    isPresent(item.volumeRatio) ||
    isPresent(item.turnoverRate);

  return (
    <>
      <span
        ref={triggerRef}
        data-testid="history-advice-trigger"
        className="inline-flex min-w-0 cursor-help"
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
        onFocus={scheduleOpen}
        onBlur={scheduleClose}
        onKeyDown={handleKeyDown}
      >
        <Badge
          variant={getDecisionActionTone(item.action, item.actionLabel, item.operationAdvice)}
          size="sm"
          className="max-w-[6rem] shadow-none transition-shadow hover:ring-1 hover:ring-primary/35"
        >
          <span className="min-w-0 truncate">{adviceText}</span>
        </Badge>
      </span>

      {typeof document !== 'undefined' && open
        ? createPortal(
            <div
              ref={panelRef}
              role="dialog"
              aria-label={t('stockTrend.adviceDetailTitle')}
              data-testid="history-advice-panel"
              style={{
                position: 'fixed',
                top: placement.top,
                left: placement.left,
                width: placement.width,
              }}
              className="@container animate-in fade-in zoom-in z-[125] max-h-[72vh] overflow-y-auto overscroll-contain rounded-2xl border border-border/60 bg-elevated/95 transition-none shadow-[0_28px_70px_rgba(3,8,20,0.32)] ring-1 ring-primary/10 ring-inset backdrop-blur-2xl duration-200"
              onMouseEnter={cancelClose}
              onMouseLeave={scheduleClose}
              onKeyDown={handleKeyDown}
            >
              <div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-primary/12 to-transparent" />

              <div className="relative flex flex-col gap-2 border-b border-border/55 px-4 pt-3.5 pb-3 @[24rem]:flex-row @[24rem]:items-start @[24rem]:justify-between @[24rem]:gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] tracking-[0.18em] text-muted-text uppercase">
                    {t('stockTrend.adviceDetailTitle')}
                  </p>
                  <p className="mt-1 font-mono text-xs whitespace-nowrap text-secondary-text">
                    {formatDateTime(item.createdAt)}
                  </p>
                </div>
                <div className="flex shrink-0 flex-col items-start gap-1 @[24rem]:items-end">
                  <Badge variant={getDecisionActionTone(item.action, item.actionLabel, item.operationAdvice)} size="md">
                    {adviceText}
                  </Badge>
                  {isPresent(item.sentimentScore) ? (
                    <span className="font-mono text-xs text-secondary-text">
                      {t('stockTrend.score')} {formatNumber(item.sentimentScore, 0)}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className="relative px-4 py-3.5">
                <div className="gap-3 @[26rem]:columns-2">
                  {operationAdvice ? (
                    <PanelSection title={t('stockTrend.adviceSection')}>
                      <p className="break-words whitespace-pre-wrap">{operationAdvice}</p>
                    </PanelSection>
                  ) : null}

                  {trendPrediction ? (
                    <PanelSection title={t('stockTrend.trendPrediction')}>
                      <p className="break-words whitespace-pre-wrap">{trendPrediction}</p>
                    </PanelSection>
                  ) : null}

                  {analysisSummary ? (
                    <PanelSection title={t('stockTrend.analysisSummary')}>
                      <p className="break-words whitespace-pre-wrap">{analysisSummary}</p>
                    </PanelSection>
                  ) : null}

                  {hasMetrics ? (
                    <PanelSection title={t('stockTrend.keyMetrics')}>
                      <dl className="grid grid-cols-2 gap-x-3 gap-y-2">
                        <PanelMetric label={t('stockTrend.stockPrice')} value={formatNumber(item.currentPrice, 2)} />
                        <PanelMetric
                          label={t('stockTrend.changePct')}
                          value={formatChangePct(item.changePct)}
                          style={getPriceChangeStyle(item.changePct)}
                        />
                        <PanelMetric label={t('stockTrend.volumeRatio')} value={formatNumber(item.volumeRatio, 2)} />
                        <PanelMetric
                          label={t('stockTrend.turnoverRate')}
                          value={`${formatNumber(item.turnoverRate, 2)}${isPresent(item.turnoverRate) ? '%' : ''}`}
                        />
                      </dl>
                    </PanelSection>
                  ) : null}

                  {modelUsed ? (
                    <PanelSection title={t('stockTrend.model')}>
                      <p className="break-words">{formatModelName(modelUsed, t)}</p>
                      {formatModelName(modelUsed, t) === modelUsed ? null : (
                        <p className="mt-0.5 font-mono text-[11px] break-all text-muted-text">{modelUsed}</p>
                      )}
                    </PanelSection>
                  ) : null}
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
};
