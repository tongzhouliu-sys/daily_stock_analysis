import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../components/scanner/api/client';
import { buildScannerFollowUpPrompt } from '../scannerFollowUp';

vi.mock('../../components/scanner/api/client', () => ({ api: vi.fn() }));

describe('scanner evidence handoff', () => {
  beforeEach(() => vi.clearAllMocks());

  it('pins the source revision and preserves missing scores and risk evidence', async () => {
    vi.mocked(api).mockResolvedValue({ id: 'card-1', revision: 2, instrument: { ticker: 'AAPL' },
      quality_score: null, rule_stage: 'RESEARCH', bear_cases: ['流动性不足'], data_as_of: '2026-11-06' });
    const prompt = await buildScannerFollowUpPrompt('AAPL', 'card-1', '2');
    expect(api).toHaveBeenCalledWith('/api/signals/card-1?revision=2');
    expect(prompt).toContain('"quality_score": null');
    expect(prompt).toContain('流动性不足');
    expect(prompt).toContain('RESEARCH');
  });

  it('rejects another stock and invalid references instead of silently mixing evidence', async () => {
    vi.mocked(api).mockResolvedValue({ id: 'card-1', revision: 2, instrument: { ticker: 'MSFT' } });
    await expect(buildScannerFollowUpPrompt('AAPL', 'card-1', '2')).rejects.toThrow('不一致');
    await expect(buildScannerFollowUpPrompt('AAPL', '../private', '2')).rejects.toThrow('不完整');
    expect(api).toHaveBeenCalledTimes(1);
  });
});
