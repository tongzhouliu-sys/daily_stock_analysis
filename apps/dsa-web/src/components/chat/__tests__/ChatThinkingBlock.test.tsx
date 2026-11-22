import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ChatThinkingBlock from '../ChatThinkingBlock';
import type { ProgressStep } from '../../../stores/agentChatStore';

const steps: ProgressStep[] = [
  {
    type: 'tool_done',
    tool: 'get_daily_history',
    display_label: 'K线获取',
    success: true,
    duration: 1.2,
    detail: { record_count: 240, date_range: '2026-01-01 ~ 2026-03-20' },
    outcome: 'success',
  },
  {
    type: 'tool_done',
    tool: 'analyze_trend',
    display_label: '技术分析',
    success: true,
    duration: 0.8,
    outcome: 'success',
  },
];

const renderBlock = (overrides: Partial<React.ComponentProps<typeof ChatThinkingBlock>> = {}) => {
  const props = {
    messageId: 'message-1',
    steps,
    expanded: false,
    expandedCategories: new Set<string>(),
    onToggleCategory: vi.fn(),
    onCollapse: vi.fn(),
    language: 'zh' as const,
    ...overrides,
  };
  render(<ChatThinkingBlock {...props} />);
  return props;
};

describe('ChatThinkingBlock', () => {
  it('stays collapsed by default: summary and tags only', () => {
    renderBlock();

    expect(screen.getByText('分析过程')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-thinking-details')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chat-thinking-collapse')).not.toBeInTheDocument();
  });

  it('expands a category into a height-capped scroll container', () => {
    renderBlock({
      expanded: true,
      expandedCategories: new Set(['message-1:kline']),
    });

    const details = screen.getByTestId('chat-thinking-details');
    expect(details).toHaveClass('chat-thinking-details');
    expect(details).toHaveTextContent('K线获取');
    // Rows are single-line truncated so a long trace cannot grow the card.
    expect(details.querySelector('.chat-thinking-row-text')).toBeInTheDocument();
  });

  it('collapses every category of the message from the summary row', () => {
    const props = renderBlock({
      expanded: true,
      expandedCategories: new Set(['message-1:kline', 'message-1:technical']),
    });

    fireEvent.click(screen.getByTestId('chat-thinking-collapse'));
    expect(props.onCollapse).toHaveBeenCalledWith('message-1');
  });

  it('toggles a category tag', () => {
    const props = renderBlock();
    fireEvent.click(screen.getByRole('button', { name: /K线获取/ }));
    expect(props.onToggleCategory).toHaveBeenCalledWith('message-1', 'kline');
  });
});
