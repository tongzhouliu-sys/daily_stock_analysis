/**
 * Client-side intent inference from user messages.
 *
 * Extracts stock codes, infers markets, and detects goal/horizon/position hints
 * from natural language. These hints are sent to the backend to suppress
 * redundant questions in the intake flow.
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

// Goal patterns
const GOAL_PATTERNS = {
  worth_watching: [
    /值得.*关注/,
    /值得.*买/,
    /值得.*持有/,
    /能不能.*买/,
    /可以.*买/,
    /是否.*关注/,
    /有没有.*价值/,
    /值.*关注/,
  ],
  evaluate_holding: [
    /持仓/,
    /我.*持有/,
    /持有.*的/,
    /我的.*持仓/,
    /仓位/,
    /已买/,
    /已经.*买/,
    /我.*有/,
    /手里.*有/,
    /成本.*是/,
  ],
  understand_move: [
    /为什么.*涨/,
    /为什么.*跌/,
    /涨跌.*原因/,
    /为何.*涨/,
    /为何.*跌/,
    /怎么.*涨/,
    /怎么.*跌/,
    /啥原因/,
    /什么原因/,
  ],
  learn_method: [
    /怎么分析/,
    /分析方法/,
    /学习/,
    /如何分析/,
    /教.*分析/,
    /分析.*方法/,
    /用什么.*方法/,
  ],
};

// Horizon patterns
const HORIZON_PATTERNS = {
  days: [
    /几天/,
    /短线/,
    /日内/,
    /数日/,
    /今天/,
    /明天/,
    /这周/,
    /本周/,
    /下周/,
    /短期.*内/,
  ],
  weeks: [
    /几周/,
    /短期/,
    /1.*4.*周/,
    /一两周/,
    /两三周/,
    /月内/,
  ],
  months: [
    /几个月/,
    /中期/,
    /1.*6.*月/,
    /半年/,
    /两三个月/,
    /三四个月/,
  ],
  long: [
    /长期/,
    /半年以上/,
    /一年/,
    /数年/,
    /几年/,
    /长线/,
    /一年以上/,
    /多年/,
  ],
};

// Position patterns
const POSITION_PATTERNS = {
  not_held: [
    /没有.*持仓/,
    /未.*持有/,
    /空仓/,
    /想.*买/,
    /准备.*买/,
    /打算.*买/,
    /还没.*买/,
    /想.*入手/,
  ],
  held_protect_profit: [
    /持有.*盈利/,
    /持仓.*赚/,
    /保护.*利润/,
    /已经.*赚/,
    /有.*利润/,
    /浮盈/,
    /盈利.*保护/,
  ],
  held_control_drawdown: [
    /持有.*亏/,
    /持仓.*套/,
    /控制.*回撤/,
    /已经.*亏/,
    /被套/,
    /浮亏/,
    /亏损/,
    /套牢/,
  ],
};

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
  const text = message.toLowerCase();

  // Extract stock code
  const codes = extractStockCodesFromMessage(message, stockIndex);
  if (codes.length > 0) {
    const primaryCode = codes[0];
    const normalized = normalizeStockCode(primaryCode);
    hints.extracted_stock_code = normalized;
    hints.inferred_market = inferMarketFromCode(normalized);
  }

  // Detect goal
  for (const [goal, patterns] of Object.entries(GOAL_PATTERNS)) {
    if (patterns.some((pattern) => pattern.test(text))) {
      hints.goal = goal as IntentHints['goal'];
      break;
    }
  }

  // Detect horizon
  for (const [horizon, patterns] of Object.entries(HORIZON_PATTERNS)) {
    if (patterns.some((pattern) => pattern.test(text))) {
      hints.horizon = horizon as IntentHints['horizon'];
      break;
    }
  }

  // Detect position
  for (const [position, patterns] of Object.entries(POSITION_PATTERNS)) {
    if (patterns.some((pattern) => pattern.test(text))) {
      hints.position = position as IntentHints['position'];
      break;
    }
  }

  return hints;
}
