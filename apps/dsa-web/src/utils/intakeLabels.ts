/**
 * Readable labels for the server-controlled intake question bank.
 * The API sends stable option ids, never free text.
 */

export type IntakeLanguage = 'zh' | 'en';

const ZH: Record<string, string> = {
  a_share: 'A 股', hk: '港股', us: '美股', unsure: '暂不确定',
  worth_watching: '判断是否值得关注', evaluate_holding: '评估已有持仓',
  understand_move: '了解涨跌原因', learn_method: '学习分析方法',
  days: '数日内', weeks: '1–4 周', months: '1–6 个月', long: '6 个月以上',
  not_held: '尚未持有', held_protect_profit: '已持有，关注盈利保护',
  held_control_drawdown: '已持有，关注回撤', decline: '暂不提供',
  downside: '控制下行风险', balanced: '机会与风险均衡', tolerant: '愿意接受较大波动',
  trend_levels: '趋势与关键位置', business_valuation: '业务与估值',
  news_events: '新闻与事件', risks_counterpoints: '风险与反例',
  comprehensive: '综合分析',
  highlights: '先看重点', detailed: '详细研究', plain: '用通俗语言解释',
  proceed: '按这些条件分析', adjust: '调整条件', generic: '只做通用解读',
};

const EN: Record<string, string> = {
  a_share: 'A-shares', hk: 'Hong Kong', us: 'US stocks', unsure: 'Not sure',
  worth_watching: 'Is it worth watching', evaluate_holding: 'Review an existing holding',
  understand_move: 'Why it moved', learn_method: 'Learn the method',
  days: 'A few days', weeks: '1–4 weeks', months: '1–6 months', long: 'Over 6 months',
  not_held: 'Not holding', held_protect_profit: 'Holding, protect profit',
  held_control_drawdown: 'Holding, limit drawdown', decline: 'Prefer not to say',
  downside: 'Limit downside', balanced: 'Balance risk and opportunity',
  tolerant: 'Accept larger swings',
  trend_levels: 'Trend and key levels', business_valuation: 'Business and valuation',
  news_events: 'News and events', risks_counterpoints: 'Risks and counterpoints',
  comprehensive: 'Comprehensive',
  highlights: 'Key points first', detailed: 'Detailed research', plain: 'Plain language',
  proceed: 'Use these conditions', adjust: 'Adjust conditions', generic: 'General reading only',
};

export const BRIEF_DIMENSIONS = [
  { field: 'object_scope', zh: '分析对象', en: 'Object' },
  { field: 'goal', zh: '目标', en: 'Goal' },
  { field: 'horizon', zh: '周期', en: 'Horizon' },
  { field: 'position', zh: '持仓', en: 'Position' },
  { field: 'risk_focus', zh: '风险侧重', en: 'Risk focus' },
  { field: 'focus_areas', zh: '研究重点', en: 'Focus areas' },
  { field: 'reading_style', zh: '阅读方式', en: 'Reading style' },
] as const;

export function intakeOptionLabel(
  id: string, language: IntakeLanguage, fallback?: string,
): string {
  const labels = language === 'en' ? EN : ZH;
  return labels[id] ?? fallback ?? id.replace(/_/g, ' ');
}
