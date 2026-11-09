import { beforeEach, describe, expect, it, vi } from 'vitest';
import apiClient from '../index';
import { getParsedApiError } from '../error';
import { historyApi } from '../history';

vi.mock('../index', () => ({
  default: {
    get: vi.fn(),
  },
}));

const mockedGet = vi.mocked(apiClient.get);

describe('historyApi.getShareImage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('parses a JSON error payload delivered as a blob', async () => {
    const blob = new Blob(
      [
        JSON.stringify({
          error: 'share_image_unavailable',
          message: '分享图片生成失败：当前转图引擎 wkhtmltoimage 不可用。',
        }),
      ],
      { type: 'application/json' },
    );
    const error = Object.assign(new Error('Request failed with status code 503'), {
      response: { status: 503, statusText: 'Service Unavailable', data: blob },
    });
    mockedGet.mockRejectedValue(error);

    await expect(historyApi.getShareImage(7)).rejects.toBe(error);

    const parsed = getParsedApiError(error);
    expect(parsed.status).toBe(503);
    expect(parsed.code).toBe('share_image_unavailable');
    expect(parsed.title).toBe('分享图不可用');
    expect(parsed.message).toContain('当前转图引擎 wkhtmltoimage 不可用');
    expect(error.message).toContain('分享图不可用');
  });
});
