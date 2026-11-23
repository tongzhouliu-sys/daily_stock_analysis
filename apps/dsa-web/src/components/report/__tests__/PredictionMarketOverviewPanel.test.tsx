import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { predictionMarketApi } from '../../../api/predictionMarket';
import type { PredictionMarketOverview } from '../../../types/analysis';
import { PredictionMarketOverviewPanel } from '../PredictionMarketOverviewPanel';

vi.mock('../../../api/predictionMarket', () => ({
  predictionMarketApi: {
    getOverview: vi.fn(),
  },
}));

const overview: PredictionMarketOverview = {
  enabled: true,
  totalContracts: 214,
  byCategory: { macro: 112, crypto: 55, equity: 47 },
  bySource: { kalshi: 181, polymarket: 33 },
  topEvents: [
    {
      source: 'kalshi',
      marketId: 'KXFED-26DEC',
      question: 'Will the Fed cut rates in December?',
      impliedProbability: 0.62,
      volume24h: 420000,
    },
  ],
  attentionConcentration: 0.41,
  attitude: {
    label: '偏 risk-on',
    tilt: 0.2,
    riskOn: { count: 20, avgProbability: 0.55 },
    riskOff: { count: 15, avgProbability: 0.35 },
  },
  generatedAt: '2026-11-23T00:00:00',
};

describe('PredictionMarketOverviewPanel', () => {
  beforeEach(() => {
    vi.mocked(predictionMarketApi.getOverview).mockReset();
  });

  it('renders the market overview summary', async () => {
    vi.mocked(predictionMarketApi.getOverview).mockResolvedValue(overview);
    render(<PredictionMarketOverviewPanel language="zh" />);
    await waitFor(() => expect(screen.getByText(/活跃合约/)).toBeInTheDocument());
    expect(screen.getByText(/214/)).toBeInTheDocument();
    expect(screen.getByText(/Will the Fed cut rates/)).toBeInTheDocument();
    expect(screen.getByText(/偏 risk-on/)).toBeInTheDocument();
    expect(screen.getByText(/非投资建议/)).toBeInTheDocument();
  });

  it('renders nothing when disabled or empty', async () => {
    vi.mocked(predictionMarketApi.getOverview).mockResolvedValue({
      ...overview,
      enabled: false,
      totalContracts: 0,
    });
    const { container } = render(<PredictionMarketOverviewPanel language="zh" />);
    await waitFor(() => expect(predictionMarketApi.getOverview).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
