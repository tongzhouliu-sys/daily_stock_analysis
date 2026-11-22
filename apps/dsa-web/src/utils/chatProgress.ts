import type { ProgressStep } from '../stores/agentChatStore';

/**
 * A progress event after display normalization: ``repeat`` counts identical
 * consecutive rows that were collapsed into one line.
 */
export interface SummarizedProgressStep {
  step: ProgressStep;
  repeat: number;
}

export const isToolProgressStep = (step: ProgressStep): boolean =>
  step.type === 'tool_start' || step.type === 'tool_done';

export const isStageProgressStep = (step: ProgressStep): boolean =>
  step.type === 'stage_start' || step.type === 'stage_done';

const sameToolCall = (start: ProgressStep, done: ProgressStep): boolean => {
  if (start.call_id && done.call_id) return start.call_id === done.call_id;
  return (start.tool || '') === (done.tool || '');
};

const sameStage = (start: ProgressStep, done: ProgressStep): boolean => {
  if (start.stage_id && done.stage_id) return start.stage_id === done.stage_id;
  return (start.stage || '') === (done.stage || '');
};

/**
 * Whether two rows would render as the exact same line.
 *
 * Used to collapse repeated identical events (for example a tool retried
 * several times) into a single row with a repeat counter instead of showing
 * the same sentence over and over.
 */
const isSameDisplayRow = (a: ProgressStep, b: ProgressStep): boolean =>
  a.type === b.type
  && (a.tool || '') === (b.tool || '')
  && (a.stage || '') === (b.stage || '')
  && (a.display_label || '') === (b.display_label || '')
  && (a.message || '') === (b.message || '')
  && (a.status || '') === (b.status || '')
  && (a.outcome || '') === (b.outcome || '')
  && a.success === b.success
  && JSON.stringify(a.detail || {}) === JSON.stringify(b.detail || {});

/**
 * Normalize raw progress events for the "分析过程" detail list.
 *
 * - ``thinking`` events are pure UI chatter ("…已完成，继续深入分析…"); the
 *   merged tool/stage rows already carry the same information, so they are
 *   dropped instead of being repeated after every tool call.
 * - ``tool_start`` / ``stage_start`` are paired with their matching
 *   ``tool_done`` / ``stage_done`` so each call appears exactly once, with the
 *   final status, duration and detail attached. Orphan starts (interrupted
 *   turns) are kept so nothing silently disappears.
 * - Identical consecutive rows are collapsed into one row with ``repeat``.
 */
export const summarizeProgressSteps = (steps: ProgressStep[]): SummarizedProgressStep[] => {
  const rows: SummarizedProgressStep[] = [];
  const pendingToolStarts: number[] = [];
  const pendingStageStarts: number[] = [];

  steps.forEach((step) => {
    if (step.type === 'thinking') return;

    if (step.type === 'tool_start') {
      pendingToolStarts.push(rows.length);
      rows.push({ step, repeat: 1 });
      return;
    }

    if (step.type === 'tool_done') {
      const pendingIndex = pendingToolStarts.findIndex((index) =>
        sameToolCall(rows[index].step, step));
      if (pendingIndex >= 0) {
        const [rowIndex] = pendingToolStarts.splice(pendingIndex, 1);
        rows[rowIndex] = { step, repeat: 1 };
        return;
      }
      rows.push({ step, repeat: 1 });
      return;
    }

    if (step.type === 'stage_start') {
      pendingStageStarts.push(rows.length);
      rows.push({ step, repeat: 1 });
      return;
    }

    if (step.type === 'stage_done') {
      const pendingIndex = pendingStageStarts.findIndex((index) =>
        sameStage(rows[index].step, step));
      if (pendingIndex >= 0) {
        const [rowIndex] = pendingStageStarts.splice(pendingIndex, 1);
        rows[rowIndex] = { step, repeat: 1 };
        return;
      }
      rows.push({ step, repeat: 1 });
      return;
    }

    rows.push({ step, repeat: 1 });
  });

  const collapsed: SummarizedProgressStep[] = [];
  rows.forEach((row) => {
    const previous = collapsed[collapsed.length - 1];
    if (previous && isSameDisplayRow(previous.step, row.step)) {
      previous.repeat += row.repeat;
      return;
    }
    collapsed.push(row);
  });

  return collapsed;
};

export const isStageDoneSuccessful = (status?: string): boolean => {
  if (!status) return true;
  const normalized = status.trim().toLowerCase();
  return ['completed', 'success', 'succeeded', 'done'].includes(normalized);
};

export const getStageDoneLabel = (step: ProgressStep): string => {
  const stage = step.stage || 'stage';
  if (step.message) return step.message;
  if (isStageDoneSuccessful(step.status)) return `${stage} completed`;
  return `${stage} ${step.status || 'finished'}`;
};

export const getPipelineBudgetSkippedLabel = (step: ProgressStep): string => {
  if (step.message) return step.message;
  return `${step.stage || 'pipeline'} skipped: insufficient budget`;
};

const STAGE_AGENT_NAMES: Record<string, [string, string]> = {
  technical: ['技术分析 Agent', 'Technical agent'],
  intel: ['情报 Agent', 'Intelligence agent'],
  risk: ['风险 Agent', 'Risk agent'],
  decision: ['决策 Agent', 'Decision agent'],
  portfolio: ['组合 Agent', 'Portfolio agent'],
  agent_loop: ['分析 Agent', 'Analysis agent'],
  answer_editing: ['回答润色', 'Answer editing'],
};

/**
 * Human readable label for the newest progress step (T40).
 *
 * Extracted from `ChatPage.getCurrentStage` so both the page and the live
 * progress component share one implementation.
 */
export const describeProgressStage = (
  steps: ProgressStep[],
  language: 'zh' | 'en',
): string => {
  if (steps.length === 0) return '正在连接...';
  const last = steps[steps.length - 1];
  if (last.type === 'thinking') return last.message || 'AI 正在思考...';
  if (last.type === 'tool_start') return `${last.display_name || last.tool}...`;
  if (last.type === 'tool_done') return `${last.display_name || last.tool} 完成`;
  if (last.type === 'stage_start' || last.type === 'stage_done') {
    const agent = STAGE_AGENT_NAMES[last.stage || '']?.[language === 'en' ? 1 : 0]
      || last.stage
      || 'Agent';
    if (last.type === 'stage_start') {
      return language === 'en' ? `${agent} is working` : `${agent} 正在分析`;
    }
    const success = isStageDoneSuccessful(last.status);
    return language === 'en'
      ? `${agent} ${success ? 'completed' : (last.status || 'finished')}`
      : `${agent} ${success ? '已完成' : (last.status || '已结束')}`;
  }
  if (last.type === 'pipeline_timeout') {
    return last.message || `${last.stage || 'pipeline'} timed out`;
  }
  if (last.type === 'pipeline_budget_skipped') return getPipelineBudgetSkippedLabel(last);
  if (last.type === 'recovering') {
    return language === 'en'
      ? 'Analysis is still running in the background...'
      : '后台任务仍在运行，正在等待最新进度...';
  }
  if (last.type === 'generating') return last.message || '正在生成最终分析...';
  return '处理中...';
};
