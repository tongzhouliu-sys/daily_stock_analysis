import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import type { AnalysisReport, HistoryItem, StockHistoryFilters, StockHistoryRange } from '../../types/analysis';
import { getSentimentTone } from '../../types/analysis';
import {
  buildDecisionActionLabelMap,
  getDecisionActionLabel,
  type DecisionActionLabelMap,
} from '../../utils/decisionAction';
import { formatDateTime } from '../../utils/format';
import { Badge, Button, Card, Tooltip } from '../common';
import { DashboardStateBlock } from '../dashboard';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { AdviceDetailPopover } from './AdviceDetailPopover';
import {
  formatChangePct,
  formatHistoryTime,
  formatModelName,
  formatNumber,
  getPriceChangeStyle,
  isPresent,
} from './historyFormat';

interface StockHistoryTrendDrawerProps {
  report: AnalysisReport;
  items: HistoryItem[];
  total: number;
  hasMore: boolean;
  isLoading: boolean;
  isLoadingMore: boolean;
  error?: unknown;
  filters: StockHistoryFilters;
  onClose: () => void;
  onRangeChange: (range: StockHistoryRange) => void;
  onLoadMore: () => void;
  onSelectRecord: (recordId: number) => void;
  onRetry: () => void;
}

const RANGE_OPTIONS: Array<{ value: StockHistoryRange; labelKey: UiTextKey }> = [
  { value: 'all', labelKey: 'stockTrend.allHistory' },
  { value: '30d', labelKey: 'stockTrend.window30' },
  { value: '90d', labelKey: 'stockTrend.window90' },
];

type AdviceSource = Pick<HistoryItem, 'operationAdvice' | 'trendPrediction' | 'action' | 'actionLabel'>;

const formatAdviceParts = (item: AdviceSource, actionLabels: DecisionActionLabelMap): string[] => {
  const actionLabel = getDecisionActionLabel(item.action, item.actionLabel, null, null, actionLabels);
  const adviceText = actionLabel || item.operationAdvice?.trim();
  const parts = [actionLabel?.trim(), item.trendPrediction?.trim()]
    .filter((part): part is string => Boolean(part));
  if (!actionLabel && adviceText) {
    return [adviceText, ...(item.trendPrediction?.trim() ? [item.trendPrediction.trim()] : [])];
  }
  return parts.length ? parts : ['--'];
};

const formatAdvice = (item: AdviceSource, actionLabels: DecisionActionLabelMap): string =>
  formatAdviceParts(item, actionLabels)[0];

const summarizeView = (
  items: HistoryItem[],
  report: AnalysisReport,
  t: (key: UiTextKey, params?: Record<string, string | number>) => string,
  actionLabels: DecisionActionLabelMap,
  currentId?: number,
) => {
  const scores = items
    .map((item) => item.sentimentScore)
    .filter((score): score is number => typeof score === 'number' && Number.isFinite(score));
  const current = items.find((item) => item.id === currentId) || items[0];
  const models = new Map<string, number>();
  items.forEach((item) => {
    const model = formatModelName(item.modelUsed, t);
    models.set(model, (models.get(model) || 0) + 1);
  });

  const averageScore = scores.length
    ? scores.reduce((sum, score) => sum + score, 0) / scores.length
    : undefined;
  const modelEntries = Array.from(models.entries()).sort((a, b) => b[1] - a[1]);
  const currentModel = formatModelName(current?.modelUsed || report.meta.modelUsed, t);

  return {
    currentScore: current?.sentimentScore ?? report.summary.sentimentScore,
    currentAdvice: current
      ? formatAdvice(current, actionLabels)
      : formatAdvice({
          operationAdvice: report.summary.operationAdvice,
          action: report.summary.action,
          actionLabel: report.summary.actionLabel,
          trendPrediction: report.summary.trendPrediction,
        }, actionLabels),
    averageScore,
    latestTime: formatDateTime(items[0]?.createdAt || report.meta.createdAt),
    modelSummary: modelEntries
      .map(([model, count]) => `${model} ${t('stockTrend.modelCountSuffix', { count })}`)
      .join(' / ') || t('stockTrend.neverRecorded'),
    currentModel,
    modelCount: modelEntries.length,
  };
};

const MetricCard: React.FC<{ label: string; value: React.ReactNode; hint?: string; title?: string }> = ({
  label,
  value,
  hint,
  title,
}) => (
  <div className="rounded-xl border border-border/70 bg-background/45 px-4 py-3">
    <p className="text-xs text-secondary-text">{label}</p>
    <p className="mt-1 truncate text-lg font-semibold text-foreground" title={title}>
      {value}
    </p>
    {hint ? <p className="mt-1 text-xs text-muted-text">{hint}</p> : null}
  </div>
);

const RangeControls: React.FC<{
  filters: StockHistoryFilters;
  onRangeChange: (range: StockHistoryRange) => void;
}> = ({ filters, onRangeChange }) => {
  const { t } = useUiLanguage();

  return (
    <div className="flex flex-wrap items-center gap-2">
      {RANGE_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onRangeChange(option.value)}
          className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
            filters.range === option.value
              ? 'border-primary/50 bg-primary/10 text-primary'
              : 'border-border/70 bg-background/50 text-secondary-text hover:bg-hover hover:text-foreground'
          }`}
        >
          {t(option.labelKey)}
        </button>
      ))}
    </div>
  );
};

export const StockHistoryTrendDrawer: React.FC<StockHistoryTrendDrawerProps> = ({
  report,
  items,
  total,
  hasMore,
  isLoading,
  isLoadingMore,
  error,
  filters,
  onClose,
  onRangeChange,
  onLoadMore,
  onSelectRecord,
  onRetry,
}) => {
  const { t } = useUiLanguage();
  const currentRecordId = report.meta.id;
  const [selectedRecordId, setSelectedRecordId] = useState(currentRecordId);
  const actionLabels = useMemo(() => buildDecisionActionLabelMap(t), [t]);
  const summary = useMemo(
    () => summarizeView(items, report, t, actionLabels, currentRecordId),
    [actionLabels, currentRecordId, items, report, t],
  );

  useEffect(() => {
    setSelectedRecordId(currentRecordId);
  }, [currentRecordId]);

  return (
    <div className="space-y-4 animate-fade-in">
      <Card variant="gradient" padding="md" className="home-panel-card">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/12 text-primary">
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M4 19V5m0 14h16M8 17V9m4 8V7m4 10v-5" />
              </svg>
            </div>
            <div>
              <h2 className="text-2xl font-bold text-foreground">{t('stockTrend.title')}</h2>
              <p className="mt-1 text-sm text-secondary-text">
                {report.meta.stockName || report.meta.stockCode} · {report.meta.stockCode}
              </p>
            </div>
          </div>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {t('stockTrend.backToCurrentReport')}
          </Button>
        </div>
      </Card>

      {isLoading ? (
        <DashboardStateBlock loading title={t('stockTrend.loading')} />
      ) : error ? (
        <DashboardStateBlock
          title={t('stockTrend.loadFailed')}
          description={t('common.retry')}
          action={(
            <Button variant="secondary" size="sm" onClick={onRetry}>
              {t('stockTrend.reload')}
            </Button>
          )}
        />
      ) : items.length === 0 ? (
        <Card variant="bordered" padding="md" className="home-panel-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-foreground">{t('stockTrend.moreEmptyTitle')}</h3>
              <p className="mt-1 text-sm text-secondary-text">
                {t('stockTrend.moreEmptyDescription')}
              </p>
            </div>
            <RangeControls filters={filters} onRangeChange={onRangeChange} />
          </div>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 @[30rem]:grid-cols-2 @[50rem]:grid-cols-4">
            <MetricCard
              label={t('stockTrend.records')}
              value={t('stockTrend.modelCountSuffix', { count: total || items.length })}
              hint={t('stockTrend.latestTime', { time: summary.latestTime })}
            />
            <MetricCard label={t('stockTrend.currentAdvice')} value={summary.currentAdvice} />
            <MetricCard
              label={t('stockTrend.currentScore')}
              value={formatNumber(summary.currentScore, 0)}
              hint={t('stockTrend.averageScore', { score: formatNumber(summary.averageScore, 1) })}
            />
            <MetricCard
              label={t('stockTrend.model')}
              value={summary.currentModel}
              hint={t('stockTrend.historyModelCount', { count: summary.modelCount })}
              title={summary.modelSummary}
            />
          </div>

          <Card variant="bordered" padding="md" className="home-panel-card">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold text-foreground">{t('stockTrend.records')}</h3>
                <p className="mt-1 text-sm text-secondary-text">
                  {t('stockTrend.loadedSummary', { loaded: items.length, total: total || items.length })}
                  <span className="@[48rem]:hidden"> · {t('stockTrend.scrollHint')}</span>
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <RangeControls filters={filters} onRangeChange={onRangeChange} />
                {hasMore ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={onLoadMore}
                    isLoading={isLoadingMore}
                    loadingText={t('stockTrend.loadingMore')}
                  >
                    {t('stockTrend.loadMore')}
                  </Button>
                ) : null}
              </div>
            </div>

            <div className="mt-4 overflow-x-auto overscroll-x-contain rounded-xl border border-border/60 bg-card/30">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="border-b border-border/60 bg-background/35 text-xs text-secondary-text">
                  <tr>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.time')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.result')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.score')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.stockPrice')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.changePct')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.volumeRatio')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.turnoverRate')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.model')}</th>
                    <th className="whitespace-nowrap px-2.5 py-3 font-medium">{t('stockTrend.table.action')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/55">
                  {items.map((item) => {
                    const isSelected = item.id === selectedRecordId;
                    const adviceText = formatAdvice(item, actionLabels);
                    return (
                      <tr
                        key={item.id}
                        className={`cursor-pointer transition-colors ${
                          isSelected ? 'bg-primary/10 ring-1 ring-inset ring-primary/35' : 'hover:bg-hover/35'
                        }`}
                        onClick={() => setSelectedRecordId(item.id)}
                      >
                        <td className="whitespace-nowrap px-2.5 py-3 font-mono text-sm text-secondary-text">
                          {formatHistoryTime(item.createdAt)}
                        </td>
                        <td className="whitespace-nowrap px-2.5 py-3">
                          <AdviceDetailPopover item={item} adviceText={adviceText} />
                        </td>
                        <td className="px-2.5 py-3 font-mono tabular-nums">
                          {isPresent(item.sentimentScore) ? (
                            <Badge variant={getSentimentTone(item.sentimentScore)}>
                              {formatNumber(item.sentimentScore, 0)}
                            </Badge>
                          ) : formatNumber(item.sentimentScore, 0)}
                        </td>
                        <td className="px-2.5 py-3 font-mono text-secondary-text">
                          {formatNumber(item.currentPrice, 2)}
                        </td>
                        <td className="px-2.5 py-3 font-mono font-semibold" style={getPriceChangeStyle(item.changePct)}>
                          {formatChangePct(item.changePct)}
                        </td>
                        <td className="px-2.5 py-3 font-mono text-secondary-text">
                          {formatNumber(item.volumeRatio, 2)}
                        </td>
                        <td className="px-2.5 py-3 font-mono text-secondary-text">
                          {formatNumber(item.turnoverRate, 2)}{isPresent(item.turnoverRate) ? '%' : ''}
                        </td>
                        <td className="px-2.5 py-3 text-secondary-text">
                          <Tooltip content={item.modelUsed || t('stockTrend.noModelTitle')}>
                            <div className="max-w-[7rem] truncate">
                              {formatModelName(item.modelUsed, t)}
                            </div>
                          </Tooltip>
                        </td>
                        <td className="px-2.5 py-3">
                          <button
                            type="button"
                            className="whitespace-nowrap rounded-lg border border-primary/35 bg-primary/8 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/14"
                            onClick={(event) => {
                              event.stopPropagation();
                              onSelectRecord(item.id);
                              onClose();
                            }}
                          >
                            {t('stockTrend.report')}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
};
