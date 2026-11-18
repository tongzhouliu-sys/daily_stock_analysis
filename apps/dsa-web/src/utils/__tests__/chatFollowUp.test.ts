import { describe, expect, test, vi, beforeEach } from 'vitest';

import { historyApi } from '../../api/history';
import {
  ChatFollowUpLoadError,
  buildFollowUpPrompt,
  buildReportFollowUpContext,
  parseFollowUpRecordId,
  resolveChatReportDraft,
  sanitizeFollowUpStockName,
  sanitizeFollowUpStockCode,
} from '../chatFollowUp';
import type { AnalysisReport } from '../../types/analysis';

vi.mock('../../api/history', () => ({
  historyApi: {
    getDetail: vi.fn(),
  },
}));

function makeReport(meta: Record<string, unknown>): AnalysisReport {
  return { meta } as unknown as AnalysisReport;
}

beforeEach(() => {
  vi.mocked(historyApi.getDetail).mockReset();
});

describe('chat follow-up helpers', () => {
  test('parseFollowUpRecordId only accepts positive integer ids', () => {
    expect(parseFollowUpRecordId('42')).toBe(42);
    expect(parseFollowUpRecordId('0')).toBeUndefined();
    expect(parseFollowUpRecordId('-1')).toBeUndefined();
    expect(parseFollowUpRecordId('12.5')).toBeUndefined();
    expect(parseFollowUpRecordId('abc')).toBeUndefined();
    expect(parseFollowUpRecordId('')).toBeUndefined();
    expect(parseFollowUpRecordId(null)).toBeUndefined();
    expect(parseFollowUpRecordId('99999999999999999999')).toBeUndefined();
  });

  test('buildFollowUpPrompt prefers the display name', () => {
    expect(buildFollowUpPrompt('600519', '贵州茅台')).toBe('请深入分析 贵州茅台(600519)');
    expect(buildFollowUpPrompt('600519', null)).toBe('请深入分析 600519');
  });

  test('sanitize helpers trim hostile input', () => {
    expect(sanitizeFollowUpStockCode('600519')).toBe('600519');
    expect(sanitizeFollowUpStockCode('sz399001')).toBe('SZ399001');
    expect(sanitizeFollowUpStockCode('not a code!')).toBeNull();
    expect(sanitizeFollowUpStockCode(null)).toBeNull();
    expect(sanitizeFollowUpStockName('  贵州   茅台 ')).toBe('贵州 茅台');
    expect(sanitizeFollowUpStockName('   ')).toBeNull();
    expect(sanitizeFollowUpStockName('x'.repeat(81))).toBeNull();
    expect(sanitizeFollowUpStockName('name\u0007with\u007fctrl')).toBeNull();
    expect(sanitizeFollowUpStockName('plain name')).toBe('plain name');
  });

  test('buildReportFollowUpContext carries only the record pointer and stock hint', () => {
    expect(buildReportFollowUpContext('600519', '贵州茅台', 7)).toEqual({
      stock_code: '600519',
      stock_name: '贵州茅台',
      report_record_id: 7,
    });
  });
});

describe('resolveChatReportDraft', () => {
  test('resolves a persisted stock report with a supported type', async () => {
    vi.mocked(historyApi.getDetail).mockResolvedValue(makeReport({
      stockCode: '600519',
      stockName: '贵州茅台',
      reportType: 'full',
      createdAt: '2026-07-05T00:00:00Z',
    }));

    await expect(resolveChatReportDraft({ stockCode: '600519', recordId: 7 })).resolves.toEqual({
      recordId: 7,
      stockCode: '600519',
      stockName: '贵州茅台',
      createdAt: '2026-07-05T00:00:00Z',
    });
  });

  test('rejects with report_unavailable when the detail request fails', async () => {
    vi.mocked(historyApi.getDetail).mockRejectedValue(new Error('network down'));
    await expect(resolveChatReportDraft({ stockCode: '600519', recordId: 7 }))
      .rejects.toMatchObject({ code: 'report_unavailable' });
  });

  test('rejects with report_unavailable when the report has no meta or stock code', async () => {
    vi.mocked(historyApi.getDetail)
      .mockResolvedValueOnce({} as AnalysisReport)
      .mockResolvedValueOnce(makeReport({ stockCode: '   ', reportType: 'full' }));
    await expect(resolveChatReportDraft({ stockCode: '600519', recordId: 7 }))
      .rejects.toMatchObject({ code: 'report_unavailable' });
    await expect(resolveChatReportDraft({ stockCode: '600519', recordId: 7 }))
      .rejects.toMatchObject({ code: 'report_unavailable' });
  });

  test.each(['market_review', 'unknown_type'] as const)(
    'rejects with report_not_supported for %s reports',
    async (reportType) => {
      vi.mocked(historyApi.getDetail).mockResolvedValue(makeReport({
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType,
      }));
      await expect(resolveChatReportDraft({ stockCode: '600519', recordId: 7 }))
        .rejects.toMatchObject({ code: 'report_not_supported' });
    },
  );

  test.each([
    ['600519', '300750', undefined, false],
    ['005930.KS', '005930.KQ', undefined, false],
    ['sh000001', '000001', 'stock', false],
    ['005930.KS', '005930.KS', 'stock', true],
    ['600519', '600519', undefined, true],
    ['sh000001', 'sh000001', 'stock', true],
  ] as const)('identity check %s vs report %s -> matches=%s', async (code, reportCode, reportType, matches) => {
    vi.mocked(historyApi.getDetail).mockResolvedValue(makeReport({
      stockCode: reportCode,
      stockName: '标的',
      reportType: 'full',
      assetType: reportType,
    }));
    const promise = resolveChatReportDraft({ stockCode: code, recordId: 7 });
    if (matches) {
      await expect(promise).resolves.toMatchObject({ recordId: 7 });
    } else {
      await expect(promise).rejects.toMatchObject({ code: 'report_mismatch' });
    }
  });

  test('matches an index report via registry canonical aliases', async () => {
    const index = [{ canonicalCode: '000001.SH', displayCode: 'sh000001', assetType: 'index' as const }];
    vi.mocked(historyApi.getDetail).mockResolvedValue(makeReport({
      stockCode: '000001.SH',
      stockName: '上证指数',
      reportType: 'full',
      assetType: 'index',
    }));

    await expect(resolveChatReportDraft({ stockCode: 'sh000001', recordId: 7, index }))
      .resolves.toMatchObject({ recordId: 7 });
    await expect(resolveChatReportDraft({ stockCode: '000001.SH', recordId: 7, index }))
      .resolves.toMatchObject({ recordId: 7 });
    // An index-style alias must never match an unrelated stock report.
    vi.mocked(historyApi.getDetail).mockResolvedValue(makeReport({
      stockCode: '600519',
      reportType: 'full',
      assetType: 'stock',
    }));
    await expect(resolveChatReportDraft({ stockCode: 'sh000001', recordId: 7, index }))
      .rejects.toMatchObject({ code: 'report_mismatch' });
  });

  test('uses exact registry identity aliases without conflating SH and SZ stocks', async () => {
    const index = [{ canonicalCode: '000001.SZ', displayCode: '000001', assetType: 'stock' as const }];
    vi.mocked(historyApi.getDetail).mockResolvedValue(makeReport({
      stockCode: '000001.SZ',
      reportType: 'full',
    }));

    await expect(resolveChatReportDraft({ stockCode: '000001', recordId: 7, index }))
      .resolves.toMatchObject({ recordId: 7 });
    await expect(resolveChatReportDraft({ stockCode: 'SZ000001', recordId: 7, index }))
      .resolves.toMatchObject({ recordId: 7 });
    await expect(resolveChatReportDraft({ stockCode: 'SH000001', recordId: 7, index }))
      .rejects.toMatchObject({ code: 'report_mismatch' });
  });

  test('exposes a typed error for failed loads', () => {
    const error = new ChatFollowUpLoadError('report_mismatch');
    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe('ChatFollowUpLoadError');
    expect(error.message).toBe('report_mismatch');
  });
});
