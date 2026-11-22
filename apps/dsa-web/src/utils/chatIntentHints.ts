/**
 * Client-side intent inference from user messages.
 *
 * Extracts stock codes and infers markets. Preference inference lives in the
 * server (``intake_questions.classify_question_intent``) so the two sides cannot
 * disagree; v1 clients still send these object hints only.
 */

import { extractStockCodesFromMessage } from './chatStockCode';
import { normalizeStockCode } from './stockCode';
import type { StockIndexItem } from '../types/stockIndex';

export interface IntentHints {
  extracted_stock_code?: string;
  inferred_market?: 'a_share' | 'hk' | 'us' | 'index' | 'other';
  goal?: 'worth_watching' | 'evaluate_holding' | 'understand_move' | 'learn_method';
  horizon?: 'days' | 'weeks' | 'months' | 'long';
  position?: 'not_held' | 'held_protect_profit' | 'held_control_drawdown';
}

/**
 * Infer market from a normalized stock code.
 */
function inferMarketFromCode(normalizedCode: string): IntentHints['inferred_market'] {
  const upper = normalizedCode.toUpperCase();

  // Index patterns
  if (/^(CSI|SH|SZ)\d{6}$/i.test(upper) || /^\d{6}\.(CSI|SH|SZ)$/i.test(upper)) {
    return 'index';
  }

  // HK patterns
  if (/^HK\d{5}$/i.test(upper) || /^\d{5}$/.test(upper) || /^\d{1,5}\.HK$/i.test(upper)) {
    return 'hk';
  }

  // A-share patterns (6 digits, possibly with SH/SZ/BJ prefix/suffix)
  if (/^\d{6}$/.test(upper) || /^(SH|SZ|BJ)\d{6}$/i.test(upper) || /^\d{6}\.(SH|SZ|BJ)$/i.test(upper)) {
    return 'a_share';
  }

  // US patterns (pure letters)
  if (/^[A-Z]{1,5}$/.test(upper)) {
    return 'us';
  }

  return 'other';
}

/**
 * Extract intent hints from a user message.
 *
 * @param message - The user's input message
 * @param stockIndex - Optional stock index registry for resolving index codes
 * @returns IntentHints object with extracted/inferred values
 */
export function extractIntentHints(
  message: string,
  stockIndex?: ReadonlyArray<StockIndexItem>,
): IntentHints {
  const hints: IntentHints = {};

  // Extract stock code
  const codes = extractStockCodesFromMessage(message, stockIndex);
  if (codes.length > 0) {
    const primaryCode = codes[0];
    const normalized = normalizeStockCode(primaryCode);
    hints.extracted_stock_code = normalized;
    hints.inferred_market = inferMarketFromCode(normalized);
  }

  return hints;
}
