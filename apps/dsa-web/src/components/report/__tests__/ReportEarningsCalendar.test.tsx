import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportEarningsCalendar } from '../ReportEarningsCalendar';

describe('ReportEarningsCalendar', () => {
  it('renders the next earnings date with an estimate badge', () => {
    const { getByText } = render(
      <ReportEarningsCalendar
        calendar={{
          nextEarningsDate: '2027-01-28',
          daysToNext: 67,
          events: [{ expectedDisclosureDate: '2027-01-28', disclosureStatus: 'scheduled' }],
          coverage: 'yfinance_calendar',
          asOf: '2026-11-22',
        }}
      />,
    );
    expect(getByText('下次财报披露:')).toBeInTheDocument();
    expect(getByText('2027-01-28')).toBeInTheDocument();
    expect(getByText(/预计/)).toBeInTheDocument();
  });

  it('renders the disclosed-only state without fabricating a future date', () => {
    const { getByText } = render(
      <ReportEarningsCalendar
        calendar={{
          nextEarningsDate: null,
          daysToNext: null,
          events: [
            {
              actualDisclosureDate: '2026-10-31',
              expectedDisclosureDate: '2026-10-31',
              disclosureStatus: 'disclosed',
            },
          ],
          coverage: 'cn_reservation_disclosure',
          asOf: '2026-11-22',
        }}
      />,
    );
    expect(getByText('最近披露:')).toBeInTheDocument();
    expect(getByText('2026-10-31')).toBeInTheDocument();
    expect(getByText('暂无已发布的预约披露日期')).toBeInTheDocument();
  });

  it('renders nothing when calendar is null', () => {
    const { container } = render(<ReportEarningsCalendar calendar={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when calendar has no events', () => {
    const { container } = render(<ReportEarningsCalendar calendar={{ events: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
