import type React from 'react';
import type { ReportLanguage, ReportStrategy as ReportStrategyType } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportStrategyProps {
  strategy?: ReportStrategyType;
  language?: ReportLanguage;
}

interface StrategyItemProps {
  label: string;
  value?: string;
  tone: string;
  index: number;
}

/**
 * 把 "195.42（回抽 MA5 阻力位）" 这类值拆成主价格与括号说明，
 * 让数字成为视觉主体、说明退为辅助信息。
 */
const splitStrategyValue = (raw?: string): { main: string; note: string } => {
  const value = (raw || '').trim();
  if (!value) {
    return { main: '—', note: '' };
  }
  const bracketed = value.match(/^(.*?)[（(]\s*(.+?)\s*[）)]\s*$/s);
  if (bracketed) {
    return {
      main: bracketed[1].trim() || '—',
      note: bracketed[2].trim(),
    };
  }
  return { main: value, note: '' };
};

const StrategyItem: React.FC<StrategyItemProps> = ({
  label,
  value,
  tone,
  index,
}) => {
  const { main, note } = splitStrategyValue(value);
  const isEmpty = !value?.trim() || /^[无—–-]+$/.test(main);
  return (
    <div
      className="report-strategy-item"
      style={{ ['--report-strategy-tone' as string]: `var(${tone})` }}
    >
      <div className="report-strategy-top">
        <span className="report-strategy-label">{label}</span>
        <span className="report-strategy-index font-mono" aria-hidden="true">
          {String(index + 1).padStart(2, '0')}
        </span>
      </div>
      <div className="report-strategy-value font-mono" data-empty={isEmpty ? 'true' : undefined}>{main}</div>
      {note ? <p className="report-strategy-note">{note}</p> : null}
    </div>
  );
};

/**
 * 策略点位区组件 - 终端风格
 */
export const ReportStrategy: React.FC<ReportStrategyProps> = ({ strategy, language = 'zh' }) => {
  if (!strategy) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);

  const strategyItems = [
    {
      label: text.idealBuy,
      value: strategy.idealBuy,
      tone: '--home-strategy-buy',
    },
    {
      label: text.secondaryBuy,
      value: strategy.secondaryBuy,
      tone: '--home-strategy-secondary',
    },
    {
      label: text.stopLoss,
      value: strategy.stopLoss,
      tone: '--home-strategy-stop',
    },
    {
      label: text.takeProfit,
      value: strategy.takeProfit,
      tone: '--home-strategy-take',
    },
  ];

  return (
    <Card variant="bordered" padding="md" className="home-panel-card report-strategy">
      <DashboardPanelHeader
        eyebrow={text.strategyPoints}
        title={text.sniperLevels}
        className="report-section-head mb-4"
        titleClassName="report-section-title"
      />
      <div className="report-strategy-grid">
        {strategyItems.map((item, index) => (
          <StrategyItem key={item.label} {...item} index={index} />
        ))}
      </div>
    </Card>
  );
};
