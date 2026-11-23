import apiClient from './index';
import { toCamelCase } from './utils';
import type { PredictionMarketOverview } from '../types/analysis';

export const predictionMarketApi = {
  async getOverview(params?: { market?: string; limit?: number }): Promise<PredictionMarketOverview> {
    const response = await apiClient.get('/prediction-markets/overview', {
      params: {
        market: params?.market,
        limit: params?.limit,
      },
    });
    return toCamelCase<PredictionMarketOverview>(response.data);
  },
};
