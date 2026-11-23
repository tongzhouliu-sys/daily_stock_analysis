import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import { predictionMarketApi } from '../../api/predictionMarket';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import type { PredictionMarketOverview, ReportLanguage } from '../../types/analysis';
import { normalizeReportLanguage } from '../../utils/reportLanguage';

interface PredictionMarketOverviewPanelProps {
  language?: ReportLanguage;
  market?: string;
}

const TEXT = {
  zh: {
    eyebrow: '预测市场全景',
    title: '当前市场概览',
    contracts: '活跃合约',
    concentration: '关注集中度',
    concentrationHint: '前 5 个合约占 24h 成交额',
    focus: '关注焦点',
    attitude: '风险定价倾向',
    riskOn: 'risk-on 平均',
    riskOff: 'risk-off 平均',
    disclaimer: '基于本地预测市场事件池的启发式统计，非投资建议。',
  },
  en: {
    eyebrow: 'Prediction Markets',
    title: 'Market overview',
    contracts: 'Open contracts',
    concentration: 'Attention',
    concentrationHint: 'Top 5 share of 24h volume',
    focus: 'In focus',
    attitude: 'Risk pricing tilt',
    riskOn: 'risk-on avg',
    riskOff: 'risk-off avg',
    disclaimer: 'Heuristic stats from the local prediction-market pool; not investment advice.',
  },
  ko: {
    eyebrow: '예측 시장',
    title: '시장 개요',
    contracts: '활성 계약',
    concentration: '관심 집중도',
    concentrationHint: '상위 5개 계약의 24시간 거래대금 비중',
    focus: '관심 포커스',
    attitude: '리스크 프라이싱 성향',
    riskOn: 'risk-on 평균',
    riskOff: 'risk-off 평균',
    disclaimer: '로컬 예측 시장 풀 기반의 휴리스틱 통계이며 투자 조언이 아닙니다.',
  },
} as const;

function formatProbability(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 'N/A';
  }
  return `${Math.round(value * 100)}%`;
}

export const PredictionMarketOverviewPanel: React.FC<PredictionMarketOverviewPanelProps> = ({
  language = 'zh',
  market,
}) => {
  const [overview, setOverview] = useState<PredictionMarketOverview | null>(null);
  const seqRef = useRef(0);
  const lang = normalizeReportLanguage(language);
  const text = TEXT[lang];

  useEffect(() => {
    const seq = seqRef.current + 1;
    seqRef.current = seq;
    predictionMarketApi
      .getOverview(market ? { market } : undefined)
      .then((data) => {
        if (seq === seqRef.current) setOverview(data);
      })
      .catch(() => {
        if (seq === seqRef.current) setOverview(null);
      });
  }, [market]);

  if (!overview || !overview.enabled || overview.totalContracts <= 0) {
    return null;
  }

  const categories = Object.entries(overview.byCategory || {});
  const attitude = overview.attitude;
  const topEvents = (overview.topEvents || []).slice(0, 3);

  return (
    <Card variant="bordered" padding="md" className="home-panel-card">
      <DashboardPanelHeader
        eyebrow={text.eyebrow}
        title={text.title}
        className="report-section-head mb-2"
        titleClassName="report-section-title"
      />
      <div className="flex flex-wrap items-center gap-2 text-sm text-foreground">
        <span className="font-semibold">
          {text.contracts}: {overview.totalContracts}
        </span>
        {categories.map(([name, count]) => (
          <span key={name} className="home-accent-chip px-2 py-0.5 text-xs text-muted-text">
            {name} {count}
          </span>
        ))}
      </div>
      <p className="mt-1 text-xs text-muted-text">
        {text.concentration}: {Math.round((overview.attentionConcentration || 0) * 100)}%
        （{text.concentrationHint}）
      </p>

      {topEvents.length > 0 ? (
        <div className="mt-2">
          <p className="text-xs font-semibold text-secondary-text">{text.focus}</p>
          <ul className="mt-1 space-y-1">
            {topEvents.map((event, index) => (
              <li key={`${event.source}:${event.marketId || index}`} className="text-xs text-secondary-text">
                <span className="font-semibold text-foreground">{formatProbability(event.impliedProbability)}</span>{' '}
                {event.question}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {attitude ? (
        <p className="mt-2 text-xs text-secondary-text">
          {text.attitude}: <span className="font-semibold text-foreground">{attitude.label}</span>
          {' · '}
          {text.riskOn} {formatProbability(attitude.riskOn?.avgProbability)}（{attitude.riskOn?.count ?? 0}）
          {' · '}
          {text.riskOff} {formatProbability(attitude.riskOff?.avgProbability)}（{attitude.riskOff?.count ?? 0}）
        </p>
      ) : null}

      <p className="mt-2 text-[11px] leading-5 text-muted-text">{text.disclaimer}</p>
    </Card>
  );
};
