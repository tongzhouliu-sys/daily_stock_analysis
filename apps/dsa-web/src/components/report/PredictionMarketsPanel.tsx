import type React from 'react';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import type { PredictionMarketEvent, ReportLanguage } from '../../types/analysis';
import { normalizeReportLanguage } from '../../utils/reportLanguage';

interface PredictionMarketsPanelProps {
  events?: PredictionMarketEvent[];
  language?: ReportLanguage;
}

const TEXT = {
  zh: {
    eyebrow: '预测市场',
    title: '事件概率',
    volume: '24h成交额',
    closeTime: '结算',
    source: '来源',
    disclaimer: '预测市场概率为群体定价，非投资建议，可能受流动性与合规限制影响。',
  },
  en: {
    eyebrow: 'Prediction Markets',
    title: 'Event odds',
    volume: '24h volume',
    closeTime: 'Close',
    source: 'Source',
    disclaimer: 'Prediction-market odds are crowd pricing, not investment advice; liquidity and regulatory limits apply.',
  },
  ko: {
    eyebrow: '예측 시장',
    title: '이벤트 확률',
    volume: '24시간 거래대금',
    closeTime: '정산',
    source: '출처',
    disclaimer: '예측 시장 확률은 군중 가격이며 투자 조언이 아닙니다. 유동성 및 규제 제한이 적용될 수 있습니다.',
  },
} as const;

function formatProbability(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  return `${Math.round(value * 100)}%`;
}

function formatVolume(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value);
}

function formatDate(value?: string | null): string {
  if (!value) {
    return '';
  }
  return String(value).slice(0, 10);
}

function hostOf(url?: string | null): string | null {
  if (!url) {
    return null;
  }
  try {
    return new URL(url).hostname.replace(/^www\./i, '');
  } catch {
    return null;
  }
}

/** Structured prediction-market evidence card (authoritative fallback for LLM text). */
export const PredictionMarketsPanel: React.FC<PredictionMarketsPanelProps> = ({
  events,
  language = 'zh',
}) => {
  if (!events || events.length === 0) {
    return null;
  }
  const lang = normalizeReportLanguage(language);
  const text = TEXT[lang];

  return (
    <Card variant="bordered" padding="md" className="home-panel-card">
      <DashboardPanelHeader
        eyebrow={text.eyebrow}
        title={text.title}
        className="report-section-head mb-2"
        titleClassName="report-section-title"
      />
      <ul className="space-y-2">
        {events.map((event, index) => {
          const url = event.marketUrl || '';
          const host = hostOf(url);
          return (
            <li
              key={`${event.source}:${event.marketId}:${index}`}
              className="rounded-lg border border-border/60 px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <p className="min-w-0 flex-1 text-sm leading-5 text-foreground">{event.question}</p>
                <span className="home-accent-chip shrink-0 px-2 py-0.5 text-xs font-semibold text-foreground">
                  {formatProbability(event.impliedProbability)}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-text">
                <span>
                  {text.volume}: {formatVolume(event.volume24h)}
                </span>
                {event.closeTime ? (
                  <span>
                    {text.closeTime}: {formatDate(event.closeTime)}
                  </span>
                ) : null}
                {url ? (
                  <a
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                    className="home-accent-link"
                  >
                    {text.source}: {host || text.source}
                  </a>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs leading-5 text-muted-text">{text.disclaimer}</p>
    </Card>
  );
};
