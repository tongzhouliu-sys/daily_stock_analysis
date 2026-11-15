import { memo } from 'react';
import { cn } from '../../utils/cn';
import type { ProgressStep } from '../../stores/agentChatStore';
import {
  getPipelineBudgetSkippedLabel,
  getStageDoneLabel,
  isStageDoneSuccessful,
} from '../../utils/chatProgress';

interface ChatThinkingBlockProps {
  messageId: string;
  steps: ProgressStep[];
  expanded: boolean;
  expandedCategories: Set<string>;
  onToggleCategory: (messageId: string, category: string) => void;
  language: 'zh' | 'en';
}

const CATEGORY_LABELS: Record<string, [string, string]> = {
  analysis_steps: ['分析步骤', 'Analysis Steps'],
  kline: ['K线获取', 'K-line Data'],
  fundamental: ['基本面分析', 'Fundamental Analysis'],
  quote: ['行情获取', 'Quote Fetch'],
  market: ['行情分析', 'Market Analysis'],
  technical: ['技术分析', 'Technical Analysis'],
  news: ['新闻搜索', 'News Search'],
  intel: ['情报搜索', 'Intel Search'],
  agent_collaboration: ['Agent 协作', 'Agent Collaboration'],
  other: ['工具调用', 'Tool Calls'],
};

function categorizeStep(step: ProgressStep): string {
  if (step.type === 'stage_start' || step.type === 'stage_done') return 'agent_collaboration';
  if (step.type !== 'tool_done' && step.type !== 'tool_start') return 'analysis_steps';
  const toolName = step.tool || '';
  if (toolName.includes('daily_history') || toolName.includes('intraday')) return 'kline';
  if (toolName.includes('stock_info') || toolName.includes('financial')) return 'fundamental';
  if (toolName.includes('realtime_quote')) return 'quote';
  if (toolName.includes('market_indices') || toolName.includes('sector')) return 'market';
  if (
    toolName.includes('trend')
    || toolName.includes('ma')
    || toolName.includes('volume')
    || toolName.includes('pattern')
  ) {
    return 'technical';
  }
  if (toolName.includes('news')) return 'news';
  if (toolName.includes('intel')) return 'intel';
  return 'other';
}

function describeStep(step: ProgressStep): { text: string; statusClass: string; iconClass: string } {
  let statusClass = 'chat-progress-item-muted';
  let iconClass = 'chat-progress-dot-muted';
  let text = '';
  switch (step.type) {
    case 'tool_done': {
      const outcome = step.outcome || (step.success ? 'success' : 'failed');
      const duration = step.duration ? ` · ${step.duration}s` : '';
      const stockCode = step.detail?.stock_code || '';
      const prefix = stockCode ? `${stockCode} · ` : '';
      text = `${prefix}${step.display_label || step.display_name || step.tool}${duration}`;
      if (outcome === 'success') {
        statusClass = 'chat-progress-item-success';
        iconClass = 'chat-progress-dot-success';
      } else if (outcome === 'timeout') {
        statusClass = 'chat-progress-item-danger';
        iconClass = 'chat-progress-dot-danger';
        text += ' · 超时';
      } else if (outcome === 'failed') {
        statusClass = 'chat-progress-item-danger';
        iconClass = 'chat-progress-dot-danger';
      } else if (outcome === 'partial') {
        text += ' · 部分可用';
      }
      if (step.detail) {
        const detailParts: string[] = [];
        if (step.detail.record_count) detailParts.push(`${step.detail.record_count} 条记录`);
        if (step.detail.date_range) detailParts.push(String(step.detail.date_range));
        if (step.detail.data_source === 'cache') detailParts.push('历史缓存');
        if (detailParts.length > 0) text += ` · ${detailParts.join(' · ')}`;
      }
      break;
    }
    case 'tool_start':
      text = `${step.display_label || step.display_name || step.tool}...`;
      statusClass = 'chat-progress-item-tool';
      iconClass = 'chat-progress-dot-tool';
      break;
    case 'stage_start':
      text = step.message || `Starting ${step.stage || 'stage'}...`;
      statusClass = 'chat-progress-item-thinking';
      iconClass = 'chat-progress-dot-thinking';
      break;
    case 'stage_done': {
      const isSuccess = isStageDoneSuccessful(step.status);
      text = getStageDoneLabel(step);
      statusClass = isSuccess ? 'chat-progress-item-success' : 'chat-progress-item-danger';
      iconClass = isSuccess ? 'chat-progress-dot-success' : 'chat-progress-dot-danger';
      break;
    }
    case 'pipeline_budget_skipped':
      text = getPipelineBudgetSkippedLabel(step);
      statusClass = 'chat-progress-item-muted';
      iconClass = 'chat-progress-dot-thinking';
      break;
    case 'thinking':
      text = step.message || `第 ${step.step} 步：思考`;
      statusClass = 'chat-progress-item-thinking';
      iconClass = 'chat-progress-dot-thinking';
      break;
    case 'generating':
      text = step.message || '生成分析';
      statusClass = 'chat-progress-item-generating';
      iconClass = 'chat-progress-dot-generating';
      break;
    default:
      text = step.message || step.type;
  }
  return { text, statusClass, iconClass };
}

/**
 * Memoized thinking/progress block (T40).
 *
 * The block only depends on its own message steps and the two expansion sets,
 * so typing in the composer no longer re-renders the historical progress tree.
 */
function ChatThinkingBlock({
  messageId,
  steps,
  expanded,
  expandedCategories,
  onToggleCategory,
  language,
}: ChatThinkingBlockProps) {
  if (!steps || steps.length === 0) return null;

  const categoryMap = new Map<string, ProgressStep[]>();
  const categoryOrder: string[] = [];
  steps.forEach((step) => {
    const category = categorizeStep(step);
    if (!categoryMap.has(category)) {
      categoryMap.set(category, []);
      categoryOrder.push(category);
    }
    categoryMap.get(category)!.push(step);
  });

  return (
    <div className="mb-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs text-muted-text opacity-60">分析过程</span>
      </div>

      {/* Category tags */}
      <div className="flex flex-wrap gap-1.5 mb-2">
        {categoryOrder.map((category) => {
          const steps = categoryMap.get(category) || [];
          const [zhLabel, enLabel] = CATEGORY_LABELS[category] || ['其他', 'Other'];
          const label = language === 'en' ? enLabel : zhLabel;
          const count = steps.length;
          const isCategoryExpanded = expanded && expandedCategories.has(`${messageId}:${category}`);

          return (
            <button
              key={category}
              onClick={() => onToggleCategory(messageId, category)}
              className={cn(
                'px-2.5 py-1 rounded text-xs transition-all',
                'border min-w-[48px] min-h-[32px]',
                isCategoryExpanded
                  ? 'bg-primary/10 border-primary text-primary'
                  : 'bg-muted/50 border-border hover:border-primary/50 text-secondary-text',
              )}
              aria-expanded={isCategoryExpanded}
            >
              <span>{label}</span>
              {count > 0 && <span className="ml-1 opacity-60">({count})</span>}
            </button>
          );
        })}
      </div>

      {/* Expanded category details */}
      {expanded && (
        <div className="space-y-3 animate-fade-in">
          {categoryOrder.map((category) => {
            if (!expandedCategories.has(`${messageId}:${category}`)) return null;
            const categorySteps = categoryMap.get(category) || [];
            return (
              <div key={category} className="pl-4 border-l-2 border-primary/30 space-y-1.5">
                {categorySteps.map((step, idx) => {
                  const { text, statusClass, iconClass } = describeStep(step);
                  return (
                    <div key={idx} className={cn('chat-progress-item', statusClass)}>
                      <span className={cn('chat-progress-dot', iconClass)} />
                      <span className="leading-relaxed">{text}</span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default memo(ChatThinkingBlock);
