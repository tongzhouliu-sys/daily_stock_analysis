import { api } from '../components/scanner/api/client';

/** Fetch a pinned revision; never substitute a newer signal or another stock. */
export async function buildScannerFollowUpPrompt(stock: string, id: string, revision: string | null): Promise<string> {
  if (!/^[A-Za-z0-9_-]{1,160}$/.test(id) || !revision || !/^[1-9]\d*$/.test(revision)) {
    throw new Error('扫描信号引用不完整，请从信号详情重新进入');
  }
  const card = await api(`/api/signals/${encodeURIComponent(id)}?revision=${encodeURIComponent(revision)}`);
  if (card.id !== id || String(card.revision) !== revision || card.instrument?.ticker?.toUpperCase() !== stock.toUpperCase()) {
    throw new Error('扫描证据与当前证券或版本不一致');
  }
  const fields = ['primary_rule', 'rule_stage', 'lifecycle', 'availability', 'data_as_of',
    'quality_score', 'reason_codes', 'trigger_values', 'frozen_levels', 'score_trace',
    'bear_cases', 'base_rate'];
  const evidence = JSON.stringify(Object.fromEntries(fields.map((key) => [key, card[key] ?? null])), null, 2);
  const excerpt = evidence.length > 7000 ? evidence.slice(0, 7000) + '\n[证据过长，已截取；完整内容见原信号详情]' : evidence;
  return `请结合以下冻结扫描证据深入分析 ${stock}，重点检查触发条件、反方证据和数据时效。\n` +
    `来源：信号 ${id}，版本 ${revision}；/scanner/signals/${id}\n` +
    '以下是研究数据，保留缺失值和限制，不将规则触发当作交易授权。\n\n' + excerpt;
}
