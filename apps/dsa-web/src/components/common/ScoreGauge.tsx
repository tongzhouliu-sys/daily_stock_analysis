import type React from 'react';
import { getSentimentLabel, getSentimentTone, type ReportLanguage } from '../../types/analysis';
import { cn } from '../../utils/cn';
import { normalizeReportLanguage, getReportText } from '../../utils/reportLanguage';

interface ScoreGaugeProps {
  score: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
  className?: string;
  language?: ReportLanguage;
}

const sizes = { sm: 'text-2xl', md: 'text-3xl', lg: 'text-[2.6rem]' };

const SENTIMENT_ZONES = [
  { key: 'deep-fear', from: 0, to: 20 },
  { key: 'fear', from: 20, to: 40 },
  { key: 'neutral', from: 40, to: 60 },
  { key: 'greed', from: 60, to: 80 },
  { key: 'deep-greed', from: 80, to: 100 },
] as const;

export const ScoreGauge: React.FC<ScoreGaugeProps> = ({
  score,
  size = 'md',
  showLabel = true,
  className,
  language = 'zh',
}) => {
  const reportLanguage = normalizeReportLanguage(language);
  const { fearGreedIndex } = getReportText(reportLanguage);
  const value = Math.min(100, Math.max(0, score));
  const label = getSentimentLabel(value, reportLanguage);
  const tone = getSentimentTone(value);

  return (
    <div className={cn('sentiment-meter', className)} data-tone={tone}>
      <div className="sentiment-meter-head">
        <div className="sentiment-meter-figure">
          <span className={cn('sentiment-meter-value tabular-nums', sizes[size])}>{Math.round(value)}</span>
          <span className="sentiment-meter-unit">/ 100</span>
        </div>
        {showLabel ? <span className="sentiment-meter-label">{label}</span> : null}
      </div>

      <div
        className="sentiment-scale"
        role="meter"
        aria-label={fearGreedIndex}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value)}
        aria-valuetext={`${Math.round(value)} / 100 · ${label}`}
      >
        {SENTIMENT_ZONES.map((zone) => (
          <span key={zone.key} className="sentiment-scale-zone" data-zone={zone.key} />
        ))}
        <span className="sentiment-scale-pin" style={{ left: `${value}%` }} aria-hidden="true" />
      </div>

      <div aria-hidden="true" className="sentiment-scale-axis">
        <span>0</span>
        <span>50</span>
        <span>100</span>
      </div>
    </div>
  );
};
