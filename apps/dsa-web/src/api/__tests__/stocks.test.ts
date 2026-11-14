import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stocksApi } from '../stocks';

const post = vi.hoisted(() => vi.fn());

vi.mock('../index', () => ({
  default: { post },
}));

describe('stocksApi', () => {
  beforeEach(() => {
    post.mockReset();
  });

  it('keeps a long holdings-image extraction request alive through the bounded retry window', async () => {
    post.mockResolvedValueOnce({
      data: {
        codes: ['600519'],
        items: [{ code: '600519', name: '贵州茅台', confidence: 'high' }],
      },
    });

    const result = await stocksApi.extractFromImage(new File(['image'], 'positions.png', {
      type: 'image/png',
    }));

    expect(post).toHaveBeenCalledWith(
      '/api/v1/stocks/extract-from-image',
      expect.any(FormData),
      {
        headers: { 'Content-Type': undefined },
        timeout: 195000,
      },
    );
    expect(result.items).toEqual([
      { code: '600519', name: '贵州茅台', confidence: 'high' },
    ]);
  });
});
