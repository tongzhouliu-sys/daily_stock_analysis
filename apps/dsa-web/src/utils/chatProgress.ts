import type { ProgressStep } from '../stores/agentChatStore';

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
