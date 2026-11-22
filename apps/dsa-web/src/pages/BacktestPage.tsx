import type React from 'react';
import { useState, useEffect, useCallback, useSyncExternalStore } from 'react';
import { Check, Minus, X } from 'lucide-react';
import { backtestApi } from '../api/backtest';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, Card, Badge, EmptyState, PageHeader, Pagination, StatusDot, Tooltip } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText, type UiLanguage } from '../i18n/uiText';
import {
  BACKTEST_DIRECTION_EXPECTED_LABELS,
  BACKTEST_MOVEMENT_LABELS,
  BACKTEST_OUTCOME_LABELS,
  BACKTEST_PHASE_FILTER_OPTIONS,
  BACKTEST_PHASE_LABELS,
  BACKTEST_STATUS_LABELS,
  BACKTEST_TEXT,
} from '../locales/featureText';
import type {
  BacktestResultItem,
  BacktestRunResponse,
  PerformanceMetrics,
  BacktestPhaseFilter,
} from '../types/backtest';
import { buildDecisionActionLabelMap, getDecisionActionLabel } from '../utils/decisionAction';
import { getMarketPhaseSummaryLabel } from '../utils/marketPhase';

const BACKTEST_INPUT_CLASS =
  'input-surface input-focus-glow h-11 w-full rounded-xl border bg-transparent px-4 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
const BACKTEST_COMPACT_INPUT_CLASS =
  'input-surface input-focus-glow h-10 rounded-xl border bg-transparent px-3 py-2 text-xs transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';
type BacktestText = (typeof BACKTEST_TEXT)[UiLanguage];

const COMPACT_RESULT_QUERY = '(max-width: 899px)';

// The result table needs horizontal room; below 900px a card list keeps every
// value readable without sideways scrolling. matchMedia is absent in jsdom, so
// the table (desktop) is the safe default.
function subscribeToCompactResultList(onChange: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {};
  const mediaQuery = window.matchMedia(COMPACT_RESULT_QUERY);
  mediaQuery.addEventListener?.('change', onChange);
  return () => mediaQuery.removeEventListener?.('change', onChange);
}

function getCompactResultListSnapshot(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(COMPACT_RESULT_QUERY).matches;
}

function useCompactResultList(): boolean {
  return useSyncExternalStore(
    subscribeToCompactResultList,
    getCompactResultListSnapshot,
    () => false,
  );
}

// ============ Helpers ============

function pct(value?: number | null): string {
  if (value == null) return '--';
  return `${value.toFixed(1)}%`;
}

function phaseLabel(row: BacktestResultItem, language: UiLanguage): string {
  const label = getMarketPhaseSummaryLabel(row.marketPhaseSummary, language);
  if (label) {
    return label
      .replace('市场阶段: ', '')
      .replace('市场阶段：', '')
      .replace('Market phase: ', '');
  }
  return (row.marketPhase ? BACKTEST_PHASE_LABELS[language][row.marketPhase] : undefined) || row.marketPhase || '--';
}

function normalizeBacktestCode(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return trimmed.toUpperCase();
}

function parseEvalWindowDays(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  const parsed = parseInt(trimmed, 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return undefined;
  }

  return parsed;
}

function labelFromMap(value: string | null | undefined, labels: Record<string, string>): string {
  if (!value) return '--';
  return labels[value] ?? value;
}

function outcomeBadge(outcome: string | undefined, language: UiLanguage) {
  const labels = BACKTEST_OUTCOME_LABELS[language];
  const label = (variant: 'success' | 'danger' | 'warning' | 'default', text: string, glow = false) => (
    <Badge variant={variant} glow={glow} className="max-w-full whitespace-nowrap">
      <span className="min-w-0 truncate">{text}</span>
    </Badge>
  );

  if (!outcome) return label('default', '--');
  switch (outcome) {
    case 'win':
      return label('success', labels.win, true);
    case 'loss':
      return label('danger', labels.loss, true);
    case 'neutral':
      return label('warning', labels.neutral);
    default:
      return label('default', outcome);
  }
}

function statusBadge(status: string, language: UiLanguage) {
  const labels = BACKTEST_STATUS_LABELS[language];
  const label = (variant: 'success' | 'warning' | 'danger' | 'default', text: string) => (
    <Badge variant={variant} className="max-w-full whitespace-nowrap">
      <span className="min-w-0 truncate">{text}</span>
    </Badge>
  );

  switch (status) {
    case 'completed':
      return label('success', labels.completed);
    case 'insufficient':
    case 'insufficient_data':
      return label('warning', labels.insufficient);
    case 'error':
      return label('danger', labels.error);
    default:
      return label('default', status);
  }
}

function actualMovementBadge(movement: string | null | undefined, language: UiLanguage) {
  const labels = BACKTEST_MOVEMENT_LABELS[language];
  switch (movement) {
    case 'up':
      return <Badge variant="success">{labels.up}</Badge>;
    case 'down':
      return <Badge variant="danger">{labels.down}</Badge>;
    case 'flat':
      return <Badge variant="warning">{labels.flat}</Badge>;
    default:
      return <Badge variant="default">--</Badge>;
  }
}

function boolIcon(value: boolean | null | undefined, text: BacktestText) {
  if (value === true) {
    return (
      <span
        className="backtest-status-chip backtest-status-chip-success"
        aria-label={text.yes}
      >
        <StatusDot tone="success" className="backtest-status-chip-dot" />
        <Check className="h-3.5 w-3.5" />
      </span>
    );
  }

  if (value === false) {
    return (
      <span
        className="backtest-status-chip backtest-status-chip-danger"
        aria-label={text.no}
      >
        <StatusDot tone="danger" className="backtest-status-chip-dot" />
        <X className="h-3.5 w-3.5" />
      </span>
    );
  }

  return (
    <span
      className="backtest-status-chip backtest-status-chip-neutral"
      aria-label={text.unknown}
    >
      <StatusDot tone="neutral" className="backtest-status-chip-dot" />
      <Minus className="h-3.5 w-3.5" />
    </span>
  );
}

// ============ Metric Row ============

const MetricRow: React.FC<{ label: string; value: string; accent?: boolean }> = ({ label, value, accent }) => (
  <div className="backtest-metric-row">
    <span className="label">{label}</span>
    <span className={`value ${accent ? 'accent' : ''}`}>{value}</span>
  </div>
);

function phaseBreakdownText(metrics: PerformanceMetrics, language: UiLanguage): string | null {
  const breakdown = metrics.diagnostics?.phaseBreakdown;
  if (!breakdown || typeof breakdown !== 'object') return null;
  const item = breakdown as Record<string, unknown>;
  const phaseLabels = BACKTEST_PHASE_LABELS[language];
  const parts = [
    [phaseLabels.premarket, item.premarket],
    [phaseLabels.intraday, item.intraday],
    [phaseLabels.postmarket, item.postmarket],
    [phaseLabels.unknown, item.unknown],
  ]
    .map(([label, value]) => `${label} ${Number(value || 0)}`)
    .join(' / ');
  return parts;
}

// ============ Performance Card ============

const PerformanceCard: React.FC<{ metrics: PerformanceMetrics; title: string; language: UiLanguage }> = ({ metrics, title, language }) => {
  const text = BACKTEST_TEXT[language];
  const phaseText = phaseBreakdownText(metrics, language);
  return (
    <Card variant="gradient" padding="md" className="backtest-performance-card animate-fade-in">
      <div className="backtest-performance-title mb-3">
        <span className="label-uppercase">{title}</span>
      </div>
      <MetricRow label={text.directionAccuracy} value={pct(metrics.directionAccuracyPct)} accent />
      <MetricRow label={text.winRate} value={pct(metrics.winRatePct)} accent />
      <MetricRow label={text.avgSimulatedReturn} value={pct(metrics.avgSimulatedReturnPct)} />
      <MetricRow label={text.avgStockReturn} value={pct(metrics.avgStockReturnPct)} />
      <MetricRow label={text.stopLossTriggerRate} value={pct(metrics.stopLossTriggerRate)} />
      <MetricRow label={text.takeProfitTriggerRate} value={pct(metrics.takeProfitTriggerRate)} />
      <MetricRow label={text.avgDaysToFirstHit} value={metrics.avgDaysToFirstHit != null ? metrics.avgDaysToFirstHit.toFixed(1) : '--'} />
      <div className="backtest-metric-footer">
        <span className="text-xs text-muted-text">{text.evaluationCount}</span>
        <span className="text-xs text-secondary-text font-mono">
          {Number(metrics.completedCount)} / {Number(metrics.totalEvaluations)}
        </span>
      </div>
      <div className="backtest-metric-outcome flex items-center justify-between">
        <span className="text-xs text-muted-text">{text.outcomeSummary}</span>
        <span className="text-xs font-mono">
          <span className="text-success">{metrics.winCount}</span>
          {' / '}
          <span className="text-danger">{metrics.lossCount}</span>
          {' / '}
          <span className="text-warning">{metrics.neutralCount}</span>
        </span>
      </div>
      {phaseText ? (
        <div className="backtest-metric-phase mt-3 border-t border-white/10 pt-2 text-xs text-muted-text">
          {formatUiText(text.phaseDistribution, { text: phaseText })}
        </div>
      ) : null}
    </Card>
  );
};

// ============ Prediction Peek ============

const PredictionPeek: React.FC<{
  row: BacktestResultItem;
  language: UiLanguage;
  text: BacktestText;
  actionLabel?: string | null;
  phaseText: string;
}> = ({ row, language, text, actionLabel, phaseText }) => {
  const expectedLabel = row.directionExpected
    ? labelFromMap(row.directionExpected, BACKTEST_DIRECTION_EXPECTED_LABELS[language])
    : '--';
  const hasReview = row.actualMovement != null
    || row.actualReturnPct != null
    || row.directionCorrect != null
    || Boolean(row.outcome);

  return (
    <div className="backtest-prediction-peek" data-testid="backtest-prediction-peek">
      <div className="backtest-peek-head">
        <div className="backtest-peek-identity">
          <span className="backtest-peek-eyebrow">{text.predictionPeekTitle}</span>
          <p className="backtest-peek-stock">
            <span className="backtest-peek-code">{row.code}</span>
            <span className="backtest-peek-name">{row.stockName || '--'}</span>
          </p>
        </div>
        {actionLabel ? (
          <Badge variant="info" size="md" className="backtest-peek-action">{actionLabel}</Badge>
        ) : null}
      </div>

      <div className="backtest-peek-meta">
        <span className="tabular-nums">{row.analysisDate || '--'}</span>
        <span className="backtest-peek-sep" aria-hidden="true" />
        <span>{phaseText}</span>
      </div>

      <div className="backtest-peek-flow">
        <section className="backtest-peek-section">
          <span className="backtest-peek-label">{text.trendView}</span>
          <p className="backtest-peek-body">{row.trendPrediction || '--'}</p>
        </section>

        <section className="backtest-peek-section">
          <span className="backtest-peek-label">{text.operationAdvice}</span>
          <p className="backtest-peek-body">{row.operationAdvice || '--'}</p>
        </section>

        {hasReview ? (
          <section className="backtest-peek-section">
            <span className="backtest-peek-label">{text.actualReview}</span>
            <dl className="backtest-peek-review">
              <div>
                <dt>{text.actualPerformance}</dt>
                <dd>
                  {actualMovementBadge(row.actualMovement, language)}
                  <span className={
                    row.actualReturnPct != null
                      ? row.actualReturnPct > 0 ? 'text-success' : row.actualReturnPct < 0 ? 'text-danger' : 'text-secondary-text'
                      : 'text-muted-text'
                  }>
                    {pct(row.actualReturnPct)}
                  </span>
                </dd>
              </div>
              <div>
                <dt>{text.directionMatch}</dt>
                <dd>
                  {boolIcon(row.directionCorrect, text)}
                  <span className="text-secondary-text">{expectedLabel}</span>
                </dd>
              </div>
              <div>
                <dt>{text.result}</dt>
                <dd>{outcomeBadge(row.outcome, language)}</dd>
              </div>
            </dl>
          </section>
        ) : null}
      </div>
    </div>
  );
};

// ============ Run Summary ============

const RunSummary: React.FC<{ data: BacktestRunResponse; language: UiLanguage }> = ({ data, language }) => {
  const text = BACKTEST_TEXT[language];
  return (
  <div className="backtest-summary animate-fade-in">
    <span className="label">{text.processed} <span className="value">{data.processed}</span></span>
    <span className="label">{text.saved} <span className="value primary">{data.saved}</span></span>
    <span className="label">{text.completed} <span className="value success">{data.completed}</span></span>
    <span className="label">{text.insufficient} <span className="value warning">{data.insufficient}</span></span>
    {data.errors > 0 && (
      <span className="label">{text.errors} <span className="value danger">{data.errors}</span></span>
    )}
    {data.message && (
      <span className="label message">{data.message}</span>
    )}
  </div>
  );
};

// ============ Main Page ============

const BacktestPage: React.FC = () => {
  const { language, t } = useUiLanguage();
  const text = BACKTEST_TEXT[language];
  const phaseFilterOptions = BACKTEST_PHASE_FILTER_OPTIONS[language];
  const actionLabels = buildDecisionActionLabelMap(t);

  // Set page title
  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  // Input state
  const [codeFilter, setCodeFilter] = useState('');
  const [analysisDateFrom, setAnalysisDateFrom] = useState('');
  const [analysisDateTo, setAnalysisDateTo] = useState('');
  const [phaseFilter, setPhaseFilter] = useState<BacktestPhaseFilter>('all');
  const [evalDays, setEvalDays] = useState('');
  const [forceRerun, setForceRerun] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<BacktestRunResponse | null>(null);
  const [runError, setRunError] = useState<ParsedApiError | null>(null);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);
  // Summary failure is reported separately: the results table can still load,
  // and the page must not look like a successfully loaded empty dataset.
  const [summaryError, setSummaryError] = useState<ParsedApiError | null>(null);
  // Pagination must use the filters that produced the visible page, not the
  // values currently sitting in the inputs (the user may have edited them
  // without pressing 筛选).
  const [appliedFilters, setAppliedFilters] = useState<{
    code?: string;
    windowDays?: number;
    startDate: string;
    endDate: string;
    phase: BacktestPhaseFilter;
  }>({ code: '', windowDays: undefined, startDate: '', endDate: '', phase: 'all' });

  // Results state
  const [results, setResults] = useState<BacktestResultItem[]>([]);
  const [totalResults, setTotalResults] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isLoadingResults, setIsLoadingResults] = useState(false);
  const pageSize = 20;

  // Performance state
  const [overallPerf, setOverallPerf] = useState<PerformanceMetrics | null>(null);
  const [stockPerf, setStockPerf] = useState<PerformanceMetrics | null>(null);
  const [isLoadingPerf, setIsLoadingPerf] = useState(false);
  const effectiveWindowDays = parseEvalWindowDays(evalDays) ?? overallPerf?.evalWindowDays;
  const isNextDayValidation = effectiveWindowDays === 1;
  const showNextDayActualColumns = isNextDayValidation;
  const isCompactResultList = useCompactResultList();

  // Fetch results
  const fetchResults = useCallback(async (
    page = 1,
    code?: string,
    windowDays?: number,
    startDate?: string,
    endDate?: string,
    phase?: BacktestPhaseFilter,
  ) => {
    setIsLoadingResults(true);
    try {
      const response = await backtestApi.getResults({
        code: code || undefined,
        evalWindowDays: windowDays,
        analysisDateFrom: startDate || undefined,
        analysisDateTo: endDate || undefined,
        analysisPhase: phase && phase !== 'all' ? phase : undefined,
        page,
        limit: pageSize,
      });
      setResults(response.items);
      setTotalResults(response.total);
      setCurrentPage(response.page);
      setPageError(null);
    } catch (err) {
      console.error('Failed to fetch backtest results:', err);
      setPageError(getParsedApiError(err));
    } finally {
      setIsLoadingResults(false);
    }
  }, []);

  // Fetch performance
  const fetchPerformance = useCallback(async (
    code?: string,
    windowDays?: number,
    startDate?: string,
    endDate?: string,
    phase?: BacktestPhaseFilter,
  ) => {
    setIsLoadingPerf(true);
    try {
      const overall = await backtestApi.getOverallPerformance({
        evalWindowDays: windowDays,
        analysisDateFrom: startDate || undefined,
        analysisDateTo: endDate || undefined,
        analysisPhase: phase && phase !== 'all' ? phase : undefined,
      });
      setOverallPerf(overall);

      if (code) {
        const stock = await backtestApi.getStockPerformance(code, {
          evalWindowDays: windowDays,
          analysisDateFrom: startDate || undefined,
          analysisDateTo: endDate || undefined,
          analysisPhase: phase && phase !== 'all' ? phase : undefined,
        });
        setStockPerf(stock);
      } else {
        setStockPerf(null);
      }
      setSummaryError(null);
      setPageError(null);
    } catch (err) {
      console.error('Failed to fetch performance:', err);
      setPageError(getParsedApiError(err));
    } finally {
      setIsLoadingPerf(false);
    }
  }, []);

  // Initial load — fetch performance first, then filter results by its window
  useEffect(() => {
    let cancelled = false;
    const init = async () => {
      let windowDays: number | undefined;
      try {
        // Get latest performance (unfiltered returns most recent summary)
        const overall = await backtestApi.getOverallPerformance();
        if (cancelled) return;
        setOverallPerf(overall);
        // Use the summary's eval_window_days to filter results consistently
        windowDays = overall?.evalWindowDays;
        if (windowDays && !evalDays) {
          setEvalDays(String(windowDays));
        }
      } catch (err) {
        if (cancelled) return;
        // A failed initialization is an explicit error, not an empty result set:
        // the table must not claim "暂无结果" when the request never succeeded.
        setSummaryError(getParsedApiError(err));
      }
      if (cancelled) return;
      setAppliedFilters({ code: '', windowDays, startDate: '', endDate: '', phase: 'all' });
      fetchResults(1, undefined, windowDays, undefined, undefined, 'all');
    };
    void init();
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Run backtest
  const handleRun = async () => {
    setIsRunning(true);
    setRunResult(null);
    setRunError(null);
    try {
      const code = normalizeBacktestCode(codeFilter);
      const requestedEvalWindowDays = parseEvalWindowDays(evalDays);
      const dateFrom = analysisDateFrom || undefined;
      const dateTo = analysisDateTo || undefined;
      const response = await backtestApi.run({
        code,
        force: forceRerun || undefined,
        minAgeDays: forceRerun ? 0 : undefined,
        evalWindowDays: requestedEvalWindowDays,
        analysisDateFrom: dateFrom,
        analysisDateTo: dateTo,
      });
      setRunResult(response);
      const effectiveEvalWindowDays =
        response.appliedEvalWindowDays
        ?? requestedEvalWindowDays
        ?? parseEvalWindowDays(evalDays)
        ?? overallPerf?.evalWindowDays;
      if (effectiveEvalWindowDays != null) {
        setEvalDays(String(effectiveEvalWindowDays));
      }
      // Refresh data with same eval_window_days
      setAppliedFilters({
        code,
        windowDays: effectiveEvalWindowDays,
        startDate: dateFrom ?? '',
        endDate: dateTo ?? '',
        phase: phaseFilter,
      });
      fetchResults(1, code, effectiveEvalWindowDays, dateFrom, dateTo, phaseFilter);
      fetchPerformance(code, effectiveEvalWindowDays, dateFrom, dateTo, phaseFilter);
    } catch (err) {
      setRunError(getParsedApiError(err));
    } finally {
      setIsRunning(false);
    }
  };

  // Filter by code
  const handleFilter = () => {
    const code = normalizeBacktestCode(codeFilter);
    const windowDays = parseEvalWindowDays(evalDays);
    setAppliedFilters({
      code,
      windowDays,
      startDate: analysisDateFrom,
      endDate: analysisDateTo,
      phase: phaseFilter,
    });
    setCurrentPage(1);
    fetchResults(1, code, windowDays, analysisDateFrom, analysisDateTo, phaseFilter);
    fetchPerformance(code, windowDays, analysisDateFrom, analysisDateTo, phaseFilter);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleFilter();
    }
  };

  const handleShowNextDay = () => {
    const code = normalizeBacktestCode(codeFilter);
    setEvalDays('1');
    setAppliedFilters({
      code,
      windowDays: 1,
      startDate: analysisDateFrom,
      endDate: analysisDateTo,
      phase: phaseFilter,
    });
    setCurrentPage(1);
    fetchResults(1, code, 1, analysisDateFrom, analysisDateTo, phaseFilter);
    fetchPerformance(code, 1, analysisDateFrom, analysisDateTo, phaseFilter);
  };

  // Pagination
  const totalPages = Math.ceil(totalResults / pageSize);
  const handlePageChange = (page: number) => {
    const applied = appliedFilters;
    fetchResults(
      page,
      applied.code || undefined,
      applied.windowDays,
      applied.startDate || undefined,
      applied.endDate || undefined,
      applied.phase,
    );
  };

  return (
    <div className="research-page backtest-page flex flex-col gap-4">
      <PageHeader
        title={t('layout.route.backtest.title')}
        description={t('layout.route.backtest.description')}
      />
      <header className="backtest-filter-card shrink-0 border border-border bg-card p-4">
        <div className="backtest-filter-grid">
          <div className="backtest-field backtest-field-wide">
            <label className="backtest-field-label" htmlFor="backtest-code-filter">
              {text.codeLabel}
            </label>
            <div className="backtest-field-inline">
              <input
                id="backtest-code-filter"
                type="text"
                value={codeFilter}
                onChange={(e) => setCodeFilter(e.target.value.toUpperCase())}
                onKeyDown={handleKeyDown}
                placeholder={text.codePlaceholder}
                disabled={isRunning}
                className={`${BACKTEST_INPUT_CLASS} min-w-0 flex-1`}
              />
              <button
                type="button"
                onClick={handleFilter}
                disabled={isLoadingResults}
                className="btn-secondary flex shrink-0 items-center gap-1.5 whitespace-nowrap"
              >
                {text.filter}
              </button>
            </div>
          </div>
          <div className="backtest-field">
            <label className="backtest-field-label" htmlFor="backtest-eval-days">
              {text.evalWindow}
            </label>
            <input
              id="backtest-eval-days"
              type="number"
              min={1}
              max={120}
              value={evalDays}
              onChange={(e) => setEvalDays(e.target.value)}
              placeholder="10"
              disabled={isRunning}
              className={`${BACKTEST_COMPACT_INPUT_CLASS} w-full tabular-nums`}
            />
          </div>
          <div className="backtest-field">
            <label className="backtest-field-label" htmlFor="backtest-phase">
              {text.phase}
            </label>
            <select
              id="backtest-phase"
              value={phaseFilter}
              onChange={(e) => setPhaseFilter(e.target.value as BacktestPhaseFilter)}
              disabled={isRunning}
              className={`${BACKTEST_COMPACT_INPUT_CLASS} w-full`}
            >
              {phaseFilterOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
          <div className="backtest-field">
            <label className="backtest-field-label" htmlFor="backtest-date-from">
              {text.startDate}
            </label>
            <input
              id="backtest-date-from"
              type="date"
              aria-label={text.startDateAria}
              value={analysisDateFrom}
              onChange={(e) => setAnalysisDateFrom(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isRunning}
              className={`${BACKTEST_COMPACT_INPUT_CLASS} w-full tabular-nums`}
            />
          </div>
          <div className="backtest-field">
            <label className="backtest-field-label" htmlFor="backtest-date-to">
              {text.endDate}
            </label>
            <input
              id="backtest-date-to"
              type="date"
              aria-label={text.endDateAria}
              value={analysisDateTo}
              onChange={(e) => setAnalysisDateTo(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isRunning}
              className={`${BACKTEST_COMPACT_INPUT_CLASS} w-full tabular-nums`}
            />
          </div>
        </div>
        <div className="backtest-action-row">
          <button
            type="button"
            onClick={handleShowNextDay}
            disabled={isLoadingResults || isLoadingPerf}
            className={`backtest-force-btn ${isNextDayValidation ? 'active' : ''}`}
          >
            <span className="dot" />
            {text.oneDayValidation}
          </button>
          <button
            type="button"
            onClick={() => setForceRerun(!forceRerun)}
            disabled={isRunning}
            className={`backtest-force-btn ${forceRerun ? 'active' : ''}`}
          >
            <span className="dot" />
            {text.forceRerun}
          </button>
          <button
            type="button"
            onClick={handleRun}
            disabled={isRunning}
            className="backtest-run-btn btn-primary flex items-center gap-1.5 whitespace-nowrap"
          >
            {isRunning ? (
              <>
                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                {text.running}
              </>
            ) : (
              text.runBacktest
            )}
          </button>
        </div>
        {runResult && (
          <div className="mt-3 max-w-4xl">
            <RunSummary data={runResult} language={language} />
          </div>
        )}
        {runError && (
          <ApiErrorAlert error={runError} className="mt-2 max-w-4xl" />
        )}
        <p className="mt-2 text-xs text-muted-text">
          {isNextDayValidation
            ? text.oneDayModeDescription
            : text.windowModeDescription}
        </p>
      </header>

      {/* Main content */}
      <div className="backtest-workspace">
        {/* Left sidebar - Performance */}
        <aside className="backtest-sidebar">
          {isLoadingPerf ? (
            <div className="flex items-center justify-center py-8">
              <div className="backtest-spinner sm" />
            </div>
          ) : overallPerf ? (
            <PerformanceCard metrics={overallPerf} title={text.overallPerformance} language={language} />
          ) : (
            <EmptyState
              title={text.noMetricsTitle}
              description={text.noMetricsDescription}
              className="h-full min-h-[12rem] border-dashed bg-card/45 shadow-none"
            />
          )}

          {stockPerf && (
            <PerformanceCard metrics={stockPerf} title={`${stockPerf.code || codeFilter}`} language={language} />
          )}
        </aside>

        {/* Right content - Results table */}
        <section className="backtest-results">
          {summaryError ? (
            <ApiErrorAlert error={summaryError} className="mb-3" />
          ) : null}
          {pageError ? (
            <ApiErrorAlert error={pageError} className="mb-3" />
          ) : null}
          {isLoadingResults ? (
            <div className="flex flex-col items-center justify-center h-64">
              <div className="backtest-spinner md" />
              <p className="mt-3 text-secondary-text text-sm">{text.loadingResults}</p>
            </div>
          ) : results.length === 0 && !pageError ? (
            <EmptyState
              title={text.noResultsTitle}
              description={text.noResultsDescription}
              className="backtest-empty-state border-dashed"
              icon={(
                <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
              )}
            />
          ) : (
            <div className="backtest-results-body animate-fade-in">
              <div className="backtest-table-toolbar">
                <div className="backtest-table-toolbar-meta">
                  <span className="label-uppercase">{isNextDayValidation ? text.nextDayValidation : text.resultSet}</span>
                  <span className="text-xs text-secondary-text backtest-toolbar-summary">
                    {appliedFilters.code
                      ? formatUiText(text.filteredStock, { code: appliedFilters.code })
                      : text.allStocks}
                    {appliedFilters.windowDays ? ` · ${formatUiText(text.dayWindow, { days: appliedFilters.windowDays })}` : ''}
                    {appliedFilters.phase !== 'all' ? ` · ${phaseFilterOptions.find((item) => item.value === appliedFilters.phase)?.label ?? appliedFilters.phase}` : ''}
                    {appliedFilters.startDate ? ` · ${formatUiText(text.fromDate, { date: appliedFilters.startDate })}` : ''}
                    {appliedFilters.endDate ? ` · ${formatUiText(text.toDate, { date: appliedFilters.endDate })}` : ''}
                  </span>
                </div>
                <span className="backtest-table-scroll-hint">{text.scrollHint}</span>
              </div>
              {isCompactResultList ? (
                <div className="backtest-card-list" data-testid="backtest-card-list">
                  {results.map((row) => {
                    const actionLabel = getDecisionActionLabel(row.action, row.actionLabel, null, null, actionLabels);
                    const phaseText = phaseLabel(row, language);
                    const actualLabel = showNextDayActualColumns ? text.actualPerformance : text.windowReturn;
                    const directionLabel = showNextDayActualColumns ? text.accuracy : text.directionMatch;

                    return (
                      <article key={row.analysisHistoryId} className="backtest-result-card">
                        <div className="backtest-card-head">
                          <div className="min-w-0">
                            <p className="backtest-card-code">{row.code}</p>
                            <p className="backtest-card-stock-name">{row.stockName || '--'}</p>
                          </div>
                          <div className="backtest-card-badges">
                            {outcomeBadge(row.outcome, language)}
                            {statusBadge(row.evalStatus, language)}
                          </div>
                        </div>
                        <dl className="backtest-card-fields">
                          <div>
                            <dt>{text.analysisDate}</dt>
                            <dd className="tabular-nums">{row.analysisDate || '--'}</dd>
                          </div>
                          <div>
                            <dt>{text.phase}</dt>
                            <dd>{phaseText}</dd>
                          </div>
                          <div className="backtest-card-field-wide">
                            <dt>{text.aiPrediction}</dt>
                            <dd>
                              <span className="block">{actionLabel || row.trendPrediction || '--'}</span>
                              {actionLabel && row.trendPrediction ? (
                                <span className="block text-xs text-secondary-text">{row.trendPrediction}</span>
                              ) : null}
                            </dd>
                          </div>
                          <div>
                            <dt>{actualLabel}</dt>
                            <dd>
                              <span className="inline-flex items-center gap-1.5">
                                {actualMovementBadge(row.actualMovement, language)}
                                <span className={
                                  row.actualReturnPct != null
                                    ? row.actualReturnPct > 0 ? 'text-success' : row.actualReturnPct < 0 ? 'text-danger' : 'text-secondary-text'
                                    : 'text-muted-text'
                                }>
                                  {pct(row.actualReturnPct)}
                                </span>
                              </span>
                            </dd>
                          </div>
                          <div>
                            <dt>{directionLabel}</dt>
                            <dd>
                              <span className="inline-flex items-center gap-1.5">
                                {boolIcon(row.directionCorrect, text)}
                                <span className="text-muted-text">
                                  {row.directionExpected ? labelFromMap(row.directionExpected, BACKTEST_DIRECTION_EXPECTED_LABELS[language]) : ''}
                                </span>
                              </span>
                            </dd>
                          </div>
                        </dl>
                        {row.operationAdvice ? (
                          <p className="backtest-card-advice">{row.operationAdvice}</p>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              ) : (
              <div className="backtest-table-wrapper">
                <table className="backtest-table w-full text-sm">
                  <colgroup>
                    <col className="backtest-col-stock" />
                    <col className="backtest-col-date" />
                    <col className="backtest-col-phase" />
                    <col className="backtest-col-prediction" />
                    <col className="backtest-col-actual" />
                    <col className="backtest-col-direction" />
                    <col className="backtest-col-outcome" />
                    <col className="backtest-col-status" />
                  </colgroup>
                  <thead className="backtest-table-head">
                    <tr className="text-left">
                      <th scope="col" className="backtest-table-head-cell">{text.stock}</th>
                      <th scope="col" className="backtest-table-head-cell">{text.analysisDate}</th>
                      <th scope="col" className="backtest-table-head-cell">{text.phase}</th>
                      <th scope="col" className="backtest-table-head-cell">{text.aiPrediction}</th>
                      <th scope="col" className="backtest-table-head-cell">
                        {showNextDayActualColumns ? text.actualPerformance : text.windowReturn}
                      </th>
                      <th scope="col" className="backtest-table-head-cell">
                        {showNextDayActualColumns ? text.accuracy : text.directionMatch}
                      </th>
                      <th scope="col" className="backtest-table-head-cell">{text.result}</th>
                      <th scope="col" className="backtest-table-head-cell">{text.status}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((row) => {
                      const actionLabel = getDecisionActionLabel(row.action, row.actionLabel, null, null, actionLabels);
                      const predictionParts = [actionLabel, row.trendPrediction, row.operationAdvice]
                        .filter((part): part is string => Boolean(part));
                      const phaseText = phaseLabel(row, language);

                      return (
                        <tr
                          key={row.analysisHistoryId}
                          className="backtest-table-row"
                        >
                          <td className="backtest-table-cell backtest-table-code">
                            <div className="flex min-w-0 flex-col">
                              <span className="backtest-cell-line font-semibold">{row.code}</span>
                              <Tooltip content={row.stockName || undefined} className="w-full min-w-0">
                                <span className="backtest-cell-line text-xs text-muted-text">{row.stockName || '--'}</span>
                              </Tooltip>
                            </div>
                          </td>
                          <td className="backtest-table-cell text-secondary-text">
                            <span className="backtest-cell-line tabular-nums">{row.analysisDate || '--'}</span>
                          </td>
                          <td className="backtest-table-cell text-secondary-text">
                            <span className="backtest-phase-cell">{phaseText}</span>
                          </td>
                          <td className="backtest-table-cell text-foreground">
                            {predictionParts.length ? (
                              <Tooltip
                                content={(
                                  <PredictionPeek
                                    row={row}
                                    language={language}
                                    text={text}
                                    actionLabel={actionLabel}
                                    phaseText={phaseText}
                                  />
                                )}
                                focusable
                                interactive
                                variant="panel"
                                className="w-full min-w-0"
                              >
                                <div className="flex w-full min-w-0 flex-col gap-1">
                                  <span className="backtest-cell-line">{actionLabel || row.trendPrediction || '--'}</span>
                                  {actionLabel && row.trendPrediction && (
                                    <span className="backtest-cell-line text-xs text-secondary-text">{row.trendPrediction}</span>
                                  )}
                                  {row.operationAdvice && (
                                    <span className="backtest-cell-line text-xs text-secondary-text">{row.operationAdvice}</span>
                                  )}
                                </div>
                              </Tooltip>
                            ) : (
                              '--'
                            )}
                          </td>
                          <td className="backtest-table-cell">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="shrink-0">{actualMovementBadge(row.actualMovement, language)}</span>
                              <span className={
                                row.actualReturnPct != null
                                  ? row.actualReturnPct > 0 ? 'text-success' : row.actualReturnPct < 0 ? 'text-danger' : 'text-secondary-text'
                                  : 'text-muted-text'
                              }>
                                {pct(row.actualReturnPct)}
                              </span>
                            </div>
                          </td>
                          <td className="backtest-table-cell">
                            <span className="backtest-direction-cell flex min-w-0 items-center gap-2">
                              <span className="shrink-0">{boolIcon(row.directionCorrect, text)}</span>
                              <span
                                className="backtest-cell-line text-muted-text"
                              >
                                {row.directionExpected ? labelFromMap(row.directionExpected, BACKTEST_DIRECTION_EXPECTED_LABELS[language]) : ''}
                              </span>
                            </span>
                          </td>
                          <td className="backtest-table-cell">{outcomeBadge(row.outcome, language)}</td>
                          <td className="backtest-table-cell">{statusBadge(row.evalStatus, language)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              )}

              {/* Pagination */}
              <div className="backtest-results-footer">
                <Pagination
                  currentPage={currentPage}
                  totalPages={totalPages}
                  onPageChange={handlePageChange}
                />
                <p className="text-xs text-muted-text text-center">
                  {formatUiText(text.totalPage, { total: totalResults, page: currentPage, pages: Math.max(totalPages, 1) })}
                </p>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default BacktestPage;
