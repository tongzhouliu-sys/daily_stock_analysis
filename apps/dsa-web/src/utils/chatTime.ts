/**
 * Localized, deterministic chat message timestamps.
 *
 * Kept pure (an injectable `now`) so tests never depend on the wall clock or the
 * host's locale. `short` is the compact relative label shown in the thread and
 * the question outline; `full` is the absolute date/time exposed as a tooltip.
 */

export interface ChatTimestampText {
  short: string;
  full: string;
}

function pad(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function formatChatTimestamp(
  iso: string | null | undefined,
  lang: 'zh' | 'en' = 'zh',
  now: number = Date.now(),
): ChatTimestampText {
  if (!iso) return { short: '', full: '' };
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return { short: '', full: '' };

  const hh = pad(date.getHours());
  const mm = pad(date.getMinutes());
  const ss = pad(date.getSeconds());
  const hhmm = `${hh}:${mm}`;
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const year = date.getFullYear();

  const full = lang === 'zh'
    ? `${year}-${month}-${day} ${hhmm}:${ss}`
    : `${month}/${day}/${year} ${hhmm}:${ss}`;

  const diff = now - date.getTime();
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const startOfDate = new Date(date);
  startOfDate.setHours(0, 0, 0, 0);
  const dayDiff = Math.round((startOfToday.getTime() - startOfDate.getTime()) / DAY);

  const md = lang === 'zh' ? `${month}-${day}` : `${month}/${day}`;
  let short: string;

  if (dayDiff <= 0) {
    // Same calendar day (or a future clock skew): relative time.
    if (diff < MINUTE) {
      short = lang === 'zh' ? '刚刚' : 'just now';
    } else if (diff < HOUR) {
      const minutes = Math.floor(diff / MINUTE);
      short = lang === 'zh' ? `${minutes} 分钟前` : `${minutes} min ago`;
    } else {
      const hours = Math.floor(diff / HOUR);
      short = lang === 'zh' ? `${hours} 小时前` : `${hours} hr ago`;
    }
  } else if (dayDiff === 1) {
    short = lang === 'zh' ? `昨天 ${hhmm}` : `Yesterday ${hhmm}`;
  } else {
    short = `${md} ${hhmm}`;
  }

  return { short, full };
}
