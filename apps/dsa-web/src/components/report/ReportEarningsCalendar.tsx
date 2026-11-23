import type React from 'react';
import { CalendarDays } from 'lucide-react';
import type { EarningsCalendar, ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { Badge, Card } from '../common';

interface ReportEarningsCalendarProps {
  calendar?: EarningsCalendar | null;
  language?: ReportLanguage;
}

const COVERAGE_LABEL: Record<ReportLanguage, Record<string, string>> = {
  zh: { cn_reservation_disclosure: 'A股预约披露', yfinance_calendar: 'yfinance 日历' },
  en: { cn_reservation_disclosure: 'A-share reservation', yfinance_calendar: 'yfinance calendar' },
  ko: { cn_reservation_disclosure: 'A주 예약 공시', yfinance_calendar: 'yfinance 캘린더' },
};

/** Parse `YYYY-MM-DD` (or an ISO datetime) into a UTC midnight timestamp. */
const parseDay = (value?: string | null): number | null => {
  const text = (value || '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return null;
  }
  const parsed = Date.parse(`${text}T00:00:00Z`);
  return Number.isFinite(parsed) ? parsed : null;
};

const formatMonthDay = (value: string): string => value.slice(5, 10);

/**
 * 财报日历卡片：展示下一次财报披露日期（计划/预估）。
 *
 * - 有 `nextEarningsDate`：展示日期 + 预计徽章 + 距今天数；若能同时拿到
 *   「最近一次已披露日期」与 `asOf`，再画一条本周期进度条（已过 / 总周期）。
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

  const lastDisclosedDate = calendar.events
    .filter((event) => event.disclosureStatus === 'disclosed')
    .map((event) => event.actualDisclosureDate || event.expectedDisclosureDate)
    .filter((value): value is string => Boolean(value))
    .sort()
    .pop();

  const cycleStart = parseDay(lastDisclosedDate);
  const cycleEnd = parseDay(nextDate);
  const asOf = parseDay(calendar.asOf);
  const cyclePct = cycleStart !== null && cycleEnd !== null && asOf !== null && cycleEnd > cycleStart
    ? Math.round(Math.min(100, Math.max(0, ((asOf - cycleStart) / (cycleEnd - cycleStart)) * 100)))
    : null;

  if (!nextDate) {
    const latest = calendar.events.find((event) => event.disclosureStatus === 'disclosed');
    const latestDate = latest?.actualDisclosureDate || latest?.expectedDisclosureDate;
    if (!latestDate) {
      return null;
    }
    return (
      <Card variant="bordered" padding="sm" className="home-panel-card report-earnings">
        <div className="report-earnings-head">
          <span className="report-earnings-title">
            <CalendarDays className="h-3.5 w-3.5 text-muted-text" aria-hidden="true" />
            {text.earningsCalendarTitle}
          </span>
          <Badge variant="default" className="shadow-none">{text.disclosedTag}</Badge>
        </div>
        <div className="report-earnings-date font-mono">{latestDate}</div>
        <div className="report-earnings-note">{text.noScheduledDisclosure}</div>
        {coverage ? (
          <div className="report-earnings-source">
            {text.earningsCalendarSource}: {coverage}
          </div>
        ) : null}
      </Card>
    );
  }

  const daysText = typeof days === 'number'
    ? `${text.expectedTag} · ${days} ${text.daysUnit}`
    : text.expectedTag;

  return (
    <Card variant="bordered" padding="sm" className="home-panel-card report-earnings">
      <div className="report-earnings-head">
        <span className="report-earnings-title">
          <CalendarDays className="h-3.5 w-3.5 text-muted-text" aria-hidden="true" />
          {text.earningsCalendarTitle}
        </span>
        <Badge variant="info" className="shadow-none">{daysText}</Badge>
      </div>
      <div className="report-earnings-date font-mono">{nextDate}</div>

      {cyclePct !== null && lastDisclosedDate ? (
        <div
          className="report-earnings-progress"
          role="progressbar"
          aria-label={text.earningsCycleProgress}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={cyclePct}
          aria-valuetext={`${text.earningsCycleProgress} ${cyclePct}%`}
        >
          <div className="report-earnings-progress-labels">
            <span>{text.latestDisclosure} {formatMonthDay(lastDisclosedDate)}</span>
            <span>{text.earningsCycleProgress} {cyclePct}%</span>
          </div>
          <div className="report-earnings-track" aria-hidden="true">
            <span className="report-earnings-track-fill" style={{ width: `${cyclePct}%` }} />
            <span className="report-earnings-track-pin" style={{ left: `${cyclePct}%` }} />
          </div>
        </div>
      ) : null}

      {coverage ? (
        <div className="report-earnings-source">
          {text.earningsCalendarSource}: {coverage}
        </div>
      ) : null}
    </Card>
  );
};
