import type React from 'react';
import { CalendarDays } from 'lucide-react';
import type { EarningsCalendar, ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { Badge, Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';

interface ReportEarningsCalendarProps {
  calendar?: EarningsCalendar | null;
  language?: ReportLanguage;
}

const COVERAGE_LABEL: Record<ReportLanguage, Record<string, string>> = {
  zh: { cn_reservation_disclosure: 'A股预约披露', yfinance_calendar: 'yfinance 日历' },
  en: { cn_reservation_disclosure: 'A-share reservation', yfinance_calendar: 'yfinance calendar' },
  ko: { cn_reservation_disclosure: 'A주 예약 공시', yfinance_calendar: 'yfinance 캘린더' },
};

/**
 * 财报日历卡片：展示下一次财报披露日期（计划/预估）。
 *
 * - 有 `nextEarningsDate`：展示日期 + 预计徽章 + 距今天数。
 * - 无 `nextEarningsDate` 但有已披露事件：展示最近披露 + 「暂无预约披露日期」。
 * - 无 calendar / 无事件：不渲染。
 */
export const ReportEarningsCalendar: React.FC<ReportEarningsCalendarProps> = ({
  calendar,
  language = 'zh',
}) => {
  if (!calendar || !calendar.events || calendar.events.length === 0) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);
  const nextDate = calendar.nextEarningsDate;
  const days = calendar.daysToNext;
  const coverage = calendar.coverage
    ? (COVERAGE_LABEL[reportLanguage]?.[calendar.coverage] ?? calendar.coverage)
    : null;

  let body: React.ReactNode;
  if (nextDate) {
    const daysText = typeof days === 'number' ? ` · ${days} ${text.daysUnit}` : '';
    body = (
      <div className="flex flex-wrap items-center gap-2 text-sm text-foreground">
        <span className="font-medium">{text.nextEarningsDate}:</span>
        <span className="font-mono">{nextDate}</span>
        <Badge variant="info">{text.expectedTag}{daysText}</Badge>
      </div>
    );
  } else {
    const latest = calendar.events.find((event) => event.disclosureStatus === 'disclosed');
    const latestDate = latest?.actualDisclosureDate || latest?.expectedDisclosureDate;
    if (!latestDate) {
      return null;
    }
    body = (
      <div className="text-sm text-foreground">
        <span className="font-medium">{text.latestDisclosure}:</span>{' '}
        <span className="font-mono">{latestDate}</span>
        <Badge variant="default" className="ml-2">{text.disclosedTag}</Badge>
        <div className="mt-1 text-xs text-muted-text">{text.noScheduledDisclosure}</div>
      </div>
    );
  }

  return (
    <Card variant="bordered" padding="md" className="home-panel-card text-left">
      <DashboardPanelHeader
        eyebrow={text.earningsCalendarTitle}
        leading={<CalendarDays className="h-4 w-4 text-muted-text" />}
        className="mb-2"
      />
      {body}
      {coverage && (
        <div className="mt-2 text-xs text-muted-text">
          {text.earningsCalendarSource}: {coverage}{calendar.asOf ? ` · ${calendar.asOf}` : ''}
        </div>
      )}
    </Card>
  );
};
