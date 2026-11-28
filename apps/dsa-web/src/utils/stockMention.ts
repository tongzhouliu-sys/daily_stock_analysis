/**
 * Stock-picker mention helpers for the ask-stock composer.
 *
 * The picker writes a "名称（代码）" mention into the composer (never a hidden
 * structured field), so the user sees and can edit exactly what is sent. The
 * server's first-question resolver already recognizes that form, so no new API
 * field is needed.
 */

import type { StockIndexItem } from '../types/stockIndex';
import { FREE_TEXT_TICKER_DENYLIST } from './chatStockCode';

export type MentionLanguage = 'zh' | 'en';

/** True when the string contains at least one CJK character. */
export function hasCjk(value: string | undefined | null): boolean {
  return /[㐀-鿿]/.test(value ?? '');
}

type Mentionable = Pick<StockIndexItem, 'nameZh' | 'aliases' | 'displayCode'>;

/**
 * Pick a display label for a stock: its Chinese name when it has CJK, otherwise
 * the first CJK alias, otherwise nothing (English US names in the index are
 * truncated and not worth echoing).
 */
export function mentionLabel(item: Mentionable): string | null {
  if (hasCjk(item.nameZh)) return item.nameZh;
  const alias = (item.aliases ?? []).find((a) => hasCjk(a));
  return alias ?? null;
}

/**
 * The one source of truth for the composer mention text.
 *
 * ``贵州茅台（600519）`` in zh, ``贵州茅台 (600519)`` in en; a stock with no CJK
 * label renders as just its display code (``EVOA``).
 */
export function formatStockMention(
  item: Mentionable,
  language: MentionLanguage = 'zh',
): string {
  const label = mentionLabel(item);
  if (!label) return item.displayCode;
  const open = language === 'zh' ? '（' : ' (';
  const close = language === 'zh' ? '）' : ')';
  return `${label}${open}${item.displayCode}${close}`;
}

const SEPARATOR_RE = /[\s，。！？!?；;、,.:：()（）[\]{}<>"'`]/;
const VERB_PREFIXES = [
  '帮我分析', '帮我看看', '查一下', '切换到', '看一下', '分析', '看看', '看下',
  '研究', '诊断', '换成', '改看', '对比', '比较',
];

/**
 * The "fragment still being typed" for passive autocomplete.
 *
 * Takes the text after the last separator, strips a leading analysis verb
 * (optionally followed by 「一下」), and returns it only when it is 2–12 chars
 * and not a common-word ASCII ticker. Returns '' when nothing should trigger.
 */
export function deriveStockQuery(draft: string): string {
  const text = draft ?? '';
  const lastSeparator = (() => {
    let idx = -1;
    SEPARATOR_RE.lastIndex = 0;
    for (const match of text.matchAll(new RegExp(SEPARATOR_RE.source, 'g'))) {
      idx = match.index ?? idx;
    }
    return idx;
  })();
  let fragment = text.slice(lastSeparator + 1).trim();

  for (const prefix of VERB_PREFIXES) {
    if (fragment.startsWith(prefix)) {
      let rest = fragment.slice(prefix.length);
      rest = rest.replace(/^一下/, '');
      fragment = rest.trim();
      break;
    }
  }

  if (fragment.length < 2 || fragment.length > 12) return '';
  const upper = fragment.toUpperCase();
  if (/^[A-Z]+$/.test(upper) && FREE_TEXT_TICKER_DENYLIST.has(upper)) return '';
  return fragment;
}
