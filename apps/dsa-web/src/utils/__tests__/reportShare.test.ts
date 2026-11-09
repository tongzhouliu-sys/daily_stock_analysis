import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { buildReportLink, copyTextToClipboard, parseReportRecordId } from '../reportShare';

describe('parseReportRecordId', () => {
  it('accepts positive integer record ids', () => {
    expect(parseReportRecordId('17')).toBe(17);
    expect(parseReportRecordId(' 42 ')).toBe(42);
  });

  it('rejects empty, non-numeric and out-of-range values', () => {
    expect(parseReportRecordId(null)).toBeUndefined();
    expect(parseReportRecordId(undefined)).toBeUndefined();
    expect(parseReportRecordId('')).toBeUndefined();
    expect(parseReportRecordId('abc')).toBeUndefined();
    expect(parseReportRecordId('-3')).toBeUndefined();
    expect(parseReportRecordId('0')).toBeUndefined();
    expect(parseReportRecordId('1.5')).toBeUndefined();
    expect(parseReportRecordId('99999999999999999999')).toBeUndefined();
  });
});

describe('buildReportLink', () => {
  it('builds a deep link that the home page can restore', () => {
    expect(buildReportLink(17)).toBe(new URL('/?recordId=17', window.location.origin).toString());
  });

  it('rejects invalid record ids instead of emitting a broken link', () => {
    expect(() => buildReportLink(0)).toThrow(/Invalid record id/);
    expect(() => buildReportLink(Number.NaN)).toThrow(/Invalid record id/);
  });
});

describe('copyTextToClipboard', () => {
  const originalClipboard = navigator.clipboard;

  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: originalClipboard });
    vi.restoreAllMocks();
  });

  it('uses the async clipboard API when available', async () => {
    await expect(copyTextToClipboard('https://example.com/?recordId=17')).resolves.toBe(true);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('https://example.com/?recordId=17');
  });

  it('falls back to execCommand when the clipboard API rejects', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    const execCommand = vi.fn().mockReturnValue(true);
    Object.defineProperty(document, 'execCommand', { configurable: true, value: execCommand });

    await expect(copyTextToClipboard('https://example.com/?recordId=17')).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
  });

  it('reports failure so the UI can show the link instead of a fake success', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn().mockReturnValue(false),
    });

    await expect(copyTextToClipboard('https://example.com/?recordId=17')).resolves.toBe(false);
  });
});
