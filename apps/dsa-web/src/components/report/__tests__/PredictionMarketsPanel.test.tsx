import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { PredictionMarketEvent } from '../../../types/analysis';
import { PredictionMarketsPanel } from '../PredictionMarketsPanel';

const events: PredictionMarketEvent[] = [
  {
    source: 'kalshi',
    marketId: 'KXFED-26DEC',
    question: 'Will the Fed cut rates in December?',
    impliedProbability: 0.62,
    volume24h: 420000,
    closeTime: '2026-12-31T00:00:00Z',
    marketUrl: 'https://kalshi.com/markets/KXFED-26DEC',
  },
];

describe('PredictionMarketsPanel', () => {
  it('renders probability, volume, source and disclaimer', () => {
    render(<PredictionMarketsPanel events={events} language="zh" />);
    expect(screen.getByText(/Will the Fed cut rates/)).toBeInTheDocument();
    expect(screen.getByText('62%')).toBeInTheDocument();
    expect(screen.getByText(/420,000/)).toBeInTheDocument();
    expect(screen.getByText(/kalshi\.com/)).toBeInTheDocument();
    expect(screen.getByText(/非投资建议/)).toBeInTheDocument();
  });

  it('renders nothing when there are no events', () => {
    const { container } = render(<PredictionMarketsPanel events={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
