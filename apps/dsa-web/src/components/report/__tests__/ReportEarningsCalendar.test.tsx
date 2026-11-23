import { render, screen } from '@testing-library/react';
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
    expect(getByText('财报日历')).toBeInTheDocument();
    expect(getByText('2027-01-28')).toBeInTheDocument();
    expect(getByText(/预计/)).toBeInTheDocument();
  });

  it('draws the reporting-cycle progress from the last disclosure to the next one', () => {
    render(
      <ReportEarningsCalendar
        calendar={{
          nextEarningsDate: '2027-01-28',
          daysToNext: 67,
          events: [
            { expectedDisclosureDate: '2027-01-28', disclosureStatus: 'scheduled' },
            { actualDisclosureDate: '2026-10-31', disclosureStatus: 'disclosed' },
          ],
          coverage: 'cn_reservation_disclosure',
          asOf: '2026-11-22',
        }}
      />,
    );

    const progress = screen.getByRole('progressbar', { name: '本周期已过' });
    // 2026-10-31 -> 2027-01-28 is 89 days; 2026-11-22 is 22 days in.
    expect(progress).toHaveAttribute('aria-valuenow', '25');
    expect(screen.getByText('最近披露 10-31')).toBeInTheDocument();
    expect(screen.getByText('本周期已过 25%')).toBeInTheDocument();
  });

  it('omits the progress bar when the previous disclosure date is unknown', () => {
    render(
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

    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('omits the progress bar when the snapshot date is missing', () => {
    render(
      <ReportEarningsCalendar
        calendar={{
          nextEarningsDate: '2027-01-28',
          daysToNext: 67,
          events: [
            { expectedDisclosureDate: '2027-01-28', disclosureStatus: 'scheduled' },
            { actualDisclosureDate: '2026-10-31', disclosureStatus: 'disclosed' },
          ],
        }}
      />,
    );

    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
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
    expect(getByText('已披露')).toBeInTheDocument();
    expect(getByText('2026-10-31')).toBeInTheDocument();
    expect(getByText('暂无已发布的预约披露日期')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
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
