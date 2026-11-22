import { describe, expect, it } from 'vitest';
import {
  CHAT_RUN_THRESHOLDS,
  TASK_RUN_THRESHOLDS,
  activeStageIndex,
  deriveRunState,
  elapsedSecondsSince,
  formatElapsed,
  parseTimestamp,
  stageIndex,
} from '../runStatus';

describe('runStatus', () => {
  it('maps the server stage order to strip indexes', () => {
    expect(stageIndex('queued')).toBe(-1);
    expect(stageIndex('market_data')).toBe(0);
    expect(stageIndex('generating')).toBe(3);
    expect(stageIndex('finalizing')).toBe(4);
    expect(stageIndex('nonsense')).toBe(-1);
    expect(activeStageIndex('done')).toBe(5);
  });

  it('keeps queued tasks queued regardless of timestamps', () => {
    expect(deriveRunState({
      status: 'pending',
      connected: false,
      thresholds: TASK_RUN_THRESHOLDS,
      now: 1_000_000,
    })).toBe('queued');
  });

  it('reports a disconnected stream before slow/stalled', () => {
    expect(deriveRunState({
      status: 'processing',
      connected: false,
      progressUpdatedAt: '2026-03-21T08:00:00Z',
      thresholds: TASK_RUN_THRESHOLDS,
      now: parseTimestamp('2026-03-21T08:00:07Z')!,
    })).toBe('disconnected');
  });

  it('escalates from running to slow to stalled on progress silence', () => {
    const startedAt = parseTimestamp('2026-03-21T08:00:00Z')!;
    const at = (ms: number) => startedAt + ms;

    expect(deriveRunState({
      status: 'processing', startedAt, now: at(5_000), thresholds: TASK_RUN_THRESHOLDS,
    })).toBe('running');
    expect(deriveRunState({
      status: 'processing', startedAt, now: at(20_000), thresholds: TASK_RUN_THRESHOLDS,
    })).toBe('slow');
    expect(deriveRunState({
      status: 'processing', startedAt, now: at(90_000), thresholds: TASK_RUN_THRESHOLDS,
    })).toBe('stalled');
    expect(deriveRunState({
      status: 'processing', startedAt, now: at(20_000), thresholds: CHAT_RUN_THRESHOLDS,
    })).toBe('slow');
  });

  it('prefers the last client activity over the server timestamp', () => {
    const now = parseTimestamp('2026-03-21T08:01:00Z')!;
    expect(deriveRunState({
      status: 'processing',
      progressUpdatedAt: '2026-03-21T08:00:00Z',
      lastActivityAt: now - 1_000,
      thresholds: TASK_RUN_THRESHOLDS,
      now,
    })).toBe('running');
  });

  it('maps terminal task statuses', () => {
    expect(deriveRunState({ status: 'completed', thresholds: TASK_RUN_THRESHOLDS })).toBe('done');
    expect(deriveRunState({ status: 'failed', thresholds: TASK_RUN_THRESHOLDS })).toBe('failed');
    expect(deriveRunState({ status: 'cancelled', thresholds: TASK_RUN_THRESHOLDS })).toBe('cancelled');
  });

  it('formats elapsed time and clamps invalid input', () => {
    expect(formatElapsed(0)).toBe('00:00');
    expect(formatElapsed(65)).toBe('01:05');
    expect(formatElapsed(3_600)).toBe('60:00');
    expect(formatElapsed(-5)).toBe('00:00');
    expect(elapsedSecondsSince(undefined)).toBe(0);
    const start = parseTimestamp('2026-03-21T08:00:00Z')!;
    expect(elapsedSecondsSince(start, start + 5_000)).toBe(5);
  });
});
