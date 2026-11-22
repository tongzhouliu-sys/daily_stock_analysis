import { describe, expect, it } from 'vitest';
import { extractIntentHints } from '../chatIntentHints';

/**
 * Preference inference moved to the server (`intake_questions`); the client
 * now only extracts the object. These tests pin that contract so a second
 * copy of the goal/horizon rules cannot creep back in.
 */
describe('extractIntentHints (object only)', () => {
  it('extracts a CN code and market', () => {
    const hints = extractIntentHints('分析 600519 的走势');
    expect(hints.extracted_stock_code).toBe('600519');
    expect(hints.inferred_market).toBe('a_share');
  });

  it('extracts a US ticker', () => {
    const hints = extractIntentHints('说说 AAPL');
    expect(hints.extracted_stock_code).toBe('AAPL');
    expect(hints.inferred_market).toBe('us');
  });

  it('does not infer preferences on the client', () => {
    const hints = extractIntentHints('我持有茅台被套了，长期怎么看');
    expect(hints.goal).toBeUndefined();
    expect(hints.horizon).toBeUndefined();
    expect(hints.position).toBeUndefined();
  });
});
