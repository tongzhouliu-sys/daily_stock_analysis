import type React from 'react';
import type {
  ReportDetails as ReportDetailsType,
  ReportMeta,
  ReportSummary as ReportSummaryType,
} from '../../types/analysis';
import { Badge, Button, Card, ScoreGauge } from '../common';
import { formatDateTime } from '../../utils/format';
import { getMarketPhaseSummaryLabel, getPartialBarLabel } from '../../utils/marketPhase';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { ShareReportButton } from './ShareReportButton';

interface ReportOverviewProps {
  meta: ReportMeta;
  summary: ReportSummaryType;
  details?: ReportDetailsType;
  isHistory?: boolean;
  watchlist?: {
    isInWatchlist: (code: string) => boolean;
    onToggle: (code: string) => void;
    isActioning: boolean;
    actionMessage: string | null;
  };
}

type BoardStatus = 'leading' | 'lagging';

type BoardSignal = {
  status: BoardStatus;
  changePct?: number;
};

type BoardSignalMaps = {
  sectors: Map<string, BoardSignal>;
  concepts: Map<string, BoardSignal>;
};

type PreparedBoard = {
  key: string;
  name: string;
  signal?: BoardSignal;
};

const normalizeBoardName = (value?: string): string =>
  (value || '').trim().replace(/\s+/g, ' ');

const normalizeBoardType = (value?: string): 'sector' | 'concept' | null => {
  const normalized = (value || '').trim().toLowerCase();
  if (!normalized) {
    return null;
  }
  if (['行业', '行业板块', 'industry', 'sector'].includes(normalized)) {
    return 'sector';
  }
  if (['概念', '概念板块', '题材', 'concept', 'theme'].includes(normalized)) {
    return 'concept';
  }
  return null;
};

const coerceFiniteNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : undefined;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim().replace(/%$/, '');
    if (!trimmed) {
      return undefined;
    }
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
};

const buildRankingSignalMap = (rankings?: ReportDetailsType['sectorRankings']): Map<string, BoardSignal> => {
  const signalMap = new Map<string, BoardSignal>();
  const topBoards = Array.isArray(rankings?.top) ? rankings.top : [];
  const bottomBoards = Array.isArray(rankings?.bottom) ? rankings.bottom : [];

  topBoards.forEach((item) => {
    const normalizedName = normalizeBoardName(item?.name);
    const changePct = coerceFiniteNumber(item?.changePct);
    if (!normalizedName || changePct === undefined) {
      return;
    }
    signalMap.set(normalizedName, {
      status: 'leading',
      changePct,
    });
  });

  bottomBoards.forEach((item) => {
    const normalizedName = normalizeBoardName(item?.name);
    const changePct = coerceFiniteNumber(item?.changePct);
    if (!normalizedName || changePct === undefined) {
      return;
    }
    signalMap.set(normalizedName, {
      status: 'lagging',
      changePct,
    });
  });

  return signalMap;
};

const buildBoardSignalMaps = (details?: ReportDetailsType): BoardSignalMaps => ({
  sectors: buildRankingSignalMap(details?.sectorRankings),
  concepts: buildRankingSignalMap(details?.conceptRankings),
});

const resolveBoardSignal = (
  board: { name?: string; type?: string },
  signalMaps: BoardSignalMaps,
): BoardSignal | undefined => {
  const boardName = normalizeBoardName(board.name);
  if (!boardName) {
    return undefined;
  }
  const boardType = normalizeBoardType(board.type);
  if (boardType === 'sector') {
    return signalMaps.sectors.get(boardName);
  }
  if (boardType === 'concept') {
    return signalMaps.concepts.get(boardName);
  }
  const sectorSignal = signalMaps.sectors.get(boardName);
  const conceptSignal = signalMaps.concepts.get(boardName);
  if (sectorSignal && !conceptSignal) {
    return sectorSignal;
  }
  if (conceptSignal && !sectorSignal) {
    return conceptSignal;
  }
  return undefined;
};

const buildPreparedRelatedBoards = (
  boards: ReportDetailsType['belongBoards'],
  signalMaps: BoardSignalMaps,
): PreparedBoard[] => {
  if (!Array.isArray(boards)) {
    return [];
  }

  return boards.reduce<PreparedBoard[]>((preparedBoards, board, index) => {
    const boardName = normalizeBoardName(board?.name);
    if (!boardName) {
      return preparedBoards;
    }
    preparedBoards.push({
      key: `${boardName}-${board?.code || index}`,
      name: boardName,
      signal: resolveBoardSignal(board, signalMaps),
    });
    return preparedBoards;
  }, []);
};

/**
 * 报告概览与情绪评分
 */
export const ReportOverview: React.FC<ReportOverviewProps> = ({
  meta,
  summary,
  details,
  watchlist,
}) => {
  const { t } = useUiLanguage();
  const reportLanguage = normalizeReportLanguage(meta.reportLanguage);
  const text = getReportText(reportLanguage);
  const marketPhaseLabel = getMarketPhaseSummaryLabel(meta.marketPhaseSummary, reportLanguage);
  const partialBarLabel = meta.marketPhaseSummary?.isPartialBar === true
    ? getPartialBarLabel(reportLanguage)
    : null;
  const relatedBoards = (Array.isArray(details?.belongBoards) ? details.belongBoards : [])
    .filter((board) => normalizeBoardName(board?.name).length > 0);
  const boardSignals = buildBoardSignalMaps(details);
  const preparedRelatedBoards = buildPreparedRelatedBoards(relatedBoards, boardSignals);

  const priceTrend: 'up' | 'down' | 'flat' = meta.changePct === undefined || meta.changePct === null
    ? 'flat'
    : meta.changePct > 0
      ? 'up'
      : meta.changePct < 0
        ? 'down'
        : 'flat';

  const formatChangePct = (changePct: number | undefined): string => {
    if (changePct === undefined || changePct === null) return '--';
    const sign = changePct > 0 ? '+' : '';
    return `${sign}${changePct.toFixed(2)}%`;
  };

  const getBoardStatusLabel = (status: BoardStatus): string => {
    if (status === 'leading') {
      return text.leadingBoard;
    }
    return text.laggingBoard;
  };

  const renderBoardChip = (board: PreparedBoard) => (
    <div key={board.key} className="report-board-chip">
      <span className="report-board-name">{board.name}</span>
      {board.signal ? (
        <>
          <span className="report-board-signal" data-status={board.signal.status}>
            {getBoardStatusLabel(board.signal.status)}
          </span>
          {board.signal.changePct !== undefined && board.signal.changePct !== null ? (
            <span
              className="report-board-change font-mono"
              data-trend={board.signal.changePct > 0 ? 'up' : board.signal.changePct < 0 ? 'down' : 'flat'}
            >
              {formatChangePct(board.signal.changePct)}
            </span>
          ) : null}
        </>
      ) : null}
    </div>
  );

  return (
    <div className="report-sheet space-y-5">
      {/* 主信息区 - 两列布局 */}
      <div className="research-report-grid">
        {/* 左侧：股票信息与结论 */}
        <div className="min-w-0 space-y-4">
          {/* 股票头部 */}
          <Card variant="gradient" padding="md" className="home-report-hero report-hero">
            <div className="report-hero-head">
              <div className="report-hero-meta">
                <span className="report-code-chip font-mono">{meta.stockCode}</span>
                {marketPhaseLabel ? (
                  <Badge variant="info" className="shrink-0 gap-1.5 shadow-none" aria-label={marketPhaseLabel}>
                    {marketPhaseLabel}
                  </Badge>
                ) : null}
                {partialBarLabel ? (
                  <Badge variant="warning" className="shrink-0 shadow-none" aria-label={partialBarLabel}>
                    {partialBarLabel}
                  </Badge>
                ) : null}
                <span className="report-meta-date">
                  <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                  {formatDateTime(meta.createdAt)}
                </span>
              </div>
              <ShareReportButton
                recordId={meta.id}
                reportTitle={`${meta.stockName || meta.stockCode}-${meta.stockCode}`}
                reportLanguage={reportLanguage}
              />
            </div>

            <div className="report-hero-title-row">
              <h3 className="report-hero-name">
                {meta.stockName || meta.stockCode}
              </h3>
              {meta.currentPrice != null && (
                <div className="report-hero-price" data-trend={priceTrend}>
                  <span className="report-hero-price-value font-mono">{meta.currentPrice.toFixed(2)}</span>
                  {meta.changePct !== undefined && meta.changePct !== null ? (
                    <span className="report-hero-price-change font-mono">{formatChangePct(meta.changePct)}</span>
                  ) : null}
                </div>
              )}
            </div>

            {/* 关键结论 */}
            <div className="report-rule" />
            <section className="report-key-insight">
              <h4 className="report-kicker">
                <span className="report-kicker-index font-mono">01</span>
                <span>{text.keyInsights}</span>
              </h4>
              <p className="report-lead">
                {summary.analysisSummary || text.noAnalysisSummary}
              </p>
              {details?.emptyNewsDisclosure ? (
                <p
                  role="note"
                  className="mt-4 rounded-sm border border-warning/30 bg-warning/10 px-3 py-2 text-left text-sm leading-6 text-foreground"
                >
                  {details.emptyNewsDisclosure}
                </p>
              ) : null}
            </section>
          </Card>

          <div className="research-report-insights">
            {/* 操作建议 */}
            <Card
              variant="bordered"
              padding="sm"
              hoverable
              className="home-panel-card home-insight-card report-verdict-card"
              style={{ ['--home-insight-tone' as string]: 'var(--home-strategy-buy)' }}
            >
              <div className="report-verdict-top">
                <div className="report-verdict-icon bg-success/10 text-success">
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
                  </svg>
                </div>
                <span className="report-verdict-index font-mono" aria-hidden="true">01</span>
              </div>
              <h4 className="report-verdict-label">{text.actionAdvice}</h4>
              <p className="report-verdict-body">
                {summary.operationAdvice || text.noAdvice}
              </p>
            </Card>

            {/* 趋势预测 */}
            <Card
              variant="bordered"
              padding="sm"
              hoverable
              className="home-panel-card home-insight-card report-verdict-card"
              style={{ ['--home-insight-tone' as string]: 'var(--home-strategy-take)' }}
            >
              <div className="report-verdict-top">
                <div className="report-verdict-icon bg-warning/10 text-warning">
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                  </svg>
                </div>
                <span className="report-verdict-index font-mono" aria-hidden="true">02</span>
              </div>
              <h4 className="report-verdict-label">{text.trendPrediction}</h4>
              <p className="report-verdict-body">
                {summary.trendPrediction || text.noPrediction}
              </p>
            </Card>
          </div>

          {preparedRelatedBoards.length > 0 && (
            <Card variant="bordered" padding="sm" className="home-panel-card report-boards min-w-0 max-w-full text-left">
              <section aria-label={text.relatedBoards} className="min-w-0 max-w-full">
                <div className="report-section-head">
                  <div className="report-section-heading">
                    <span className="label-uppercase">{text.boardLinkage}</span>
                    <h3>{text.relatedBoards}</h3>
                  </div>
                  <span className="report-section-count font-mono">{preparedRelatedBoards.length}</span>
                </div>

                <div className="home-related-board-list flex min-h-6 w-full min-w-0 max-w-full flex-nowrap items-center gap-2 overflow-x-auto overscroll-x-contain touch-pan-x pb-1">
                  {preparedRelatedBoards.map(renderBoardChip)}
                </div>
              </section>
            </Card>
          )}
        </div>

        {/* 右侧：情绪指标 / 自选操作 */}
        <div className="report-rail flex flex-col space-y-4">
          {watchlist && meta.reportType !== 'market_review' && meta.assetType !== 'index' && (
            <Card variant="bordered" padding="sm" className="home-panel-card report-rail-watchlist">
              <div className="space-y-3">
                <span className="label-uppercase">{t('report.watchlist')}</span>
                <div className="report-rail-code font-mono">{meta.stockCode}</div>
                <Button
                  variant={watchlist.isInWatchlist(meta.stockCode) ? 'danger-subtle' : 'secondary'}
                  size="sm"
                  isLoading={watchlist.isActioning}
                  onClick={() => watchlist.onToggle(meta.stockCode)}
                  className="w-full text-xs"
                >
                  {watchlist.isInWatchlist(meta.stockCode) ? t('report.removeFromWatchlist') : t('report.addToWatchlist')}
                </Button>
                {watchlist.actionMessage && (
                  <p className="text-[11px] text-secondary-text animate-in fade-in">{watchlist.actionMessage}</p>
                )}
              </div>
            </Card>
          )}
          <Card variant="bordered" padding="md" className="home-panel-card home-rail-card report-sentiment-card !overflow-visible">
            <div className="report-section-head">
              <div className="report-section-heading">
                <span className="label-uppercase">{text.fearGreedIndex}</span>
                <h3>{text.marketSentiment}</h3>
              </div>
            </div>
            <ScoreGauge score={summary.sentimentScore} size="lg" language={reportLanguage} />
          </Card>
        </div>
      </div>
    </div>
  );
};
