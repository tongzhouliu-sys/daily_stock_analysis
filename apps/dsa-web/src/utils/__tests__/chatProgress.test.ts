import { describe, expect, it } from 'vitest';
import { summarizeProgressSteps } from '../chatProgress';
import type { ProgressStep } from '../../stores/agentChatStore';

const toolStart = (tool: string, callId: string): ProgressStep => ({
  type: 'tool_start',
  tool,
  call_id: callId,
  display_label: tool,
});

const toolDone = (
  tool: string,
  callId: string,
  extra: Partial<ProgressStep> = {},
): ProgressStep => ({
  type: 'tool_done',
  tool,
  call_id: callId,
  display_label: tool,
  success: true,
  duration: 0.4,
  outcome: 'success',
  ...extra,
});

describe('summarizeProgressSteps', () => {
  it('merges a tool start with its done event into a single row', () => {
    const rows = summarizeProgressSteps([
      toolStart('realtime_quote', 'call_1'),
      toolDone('realtime_quote', 'call_1'),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].step.type).toBe('tool_done');
    expect(rows[0].step.call_id).toBe('call_1');
    expect(rows[0].repeat).toBe(1);
  });

  it('drops repetitive thinking chatter', () => {
    const rows = summarizeProgressSteps([
      { type: 'thinking', message: '正在制定分析路径...' },
      toolStart('daily_history', 'call_1'),
      toolDone('daily_history', 'call_1'),
      { type: 'thinking', message: '「K线获取」已完成，继续深入分析...' },
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].step.tool).toBe('daily_history');
  });

  it('pairs stage start and done events', () => {
    const rows = summarizeProgressSteps([
      { type: 'stage_start', stage: 'technical', stage_id: 'stage_1' },
      { type: 'stage_done', stage: 'technical', stage_id: 'stage_1', status: 'completed' },
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].step.type).toBe('stage_done');
    expect(rows[0].step.status).toBe('completed');
  });

  it('collapses identical consecutive rows into a repeat counter', () => {
    const rows = summarizeProgressSteps([
      toolDone('news', 'call_1'),
      toolDone('news', 'call_2'),
      toolDone('news', 'call_3'),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].repeat).toBe(3);
  });

  it('keeps an orphan tool start so interrupted calls stay visible', () => {
    const rows = summarizeProgressSteps([toolStart('intraday', 'call_1')]);

    expect(rows).toHaveLength(1);
    expect(rows[0].step.type).toBe('tool_start');
  });

  it('keeps failures distinguishable from successful retries', () => {
    const rows = summarizeProgressSteps([
      toolStart('news', 'call_1'),
      toolDone('news', 'call_1', { success: false, outcome: 'failed' }),
      toolStart('news', 'call_2'),
      toolDone('news', 'call_2'),
    ]);

    expect(rows).toHaveLength(2);
    expect(rows[0].step.outcome).toBe('failed');
    expect(rows[1].step.outcome).toBe('success');
  });
});
