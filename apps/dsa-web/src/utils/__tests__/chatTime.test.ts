import { describe, expect, it } from 'vitest';
import { formatChatTimestamp } from '../chatTime';

const NOW = new Date('2026-11-23T12:00:00').getTime();

describe('formatChatTimestamp', () => {
  it('returns empty strings for missing or invalid input', () => {
    expect(formatChatTimestamp(null, 'zh', NOW)).toEqual({ short: '', full: '' });
    expect(formatChatTimestamp(undefined, 'en', NOW)).toEqual({ short: '', full: '' });
    expect(formatChatTimestamp('not-a-date', 'zh', NOW)).toEqual({ short: '', full: '' });
  });

  it('renders a full absolute timestamp in zh and en', () => {
    const iso = '2026-11-22T08:05:03';
    expect(formatChatTimestamp(iso, 'zh', NOW).full).toBe('2026-11-22 08:05:03');
    expect(formatChatTimestamp(iso, 'en', NOW).full).toBe('11/22/2026 08:05:03');
  });

  it('renders "just now" within a minute', () => {
    const iso = new Date(NOW - 30_000).toISOString();
    expect(formatChatTimestamp(iso, 'zh', NOW).short).toBe('刚刚');
    expect(formatChatTimestamp(iso, 'en', NOW).short).toBe('just now');
  });

  it('renders minutes and hours buckets', () => {
    expect(formatChatTimestamp(new Date(NOW - 5 * 60_000).toISOString(), 'zh', NOW).short)
      .toBe('5 分钟前');
    expect(formatChatTimestamp(new Date(NOW - 3 * 60 * 60_000).toISOString(), 'en', NOW).short)
      .toBe('3 hr ago');
  });

  it('renders "yesterday" for the previous calendar day', () => {
    const yesterday = '2026-11-22T23:45:00';
    expect(formatChatTimestamp(yesterday, 'zh', NOW).short).toBe('昨天 23:45');
    expect(formatChatTimestamp(yesterday, 'en', NOW).short).toBe('Yesterday 23:45');
  });

  it('falls back to a short date for older messages', () => {
    const older = '2026-11-01T09:30:00';
    expect(formatChatTimestamp(older, 'zh', NOW).short).toBe('11-01 09:30');
    expect(formatChatTimestamp(older, 'en', NOW).short).toBe('11/01 09:30');
  });
});
