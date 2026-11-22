import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ChatMessageItem from '../ChatMessageItem';
import type { Message } from '../../../stores/agentChatStore';

const renderSpy = vi.fn();

vi.mock('../ChatAnswer', () => ({
  default: ({ content }: { content: string }) => {
    renderSpy(content);
    return <div data-testid="chat-answer">{content}</div>;
  },
}));

const message: Message = {
  id: 'msg-1',
  role: 'assistant',
  content: '茅台趋势向上',
} as Message;

function buildProps() {
  return {
    message,
    skillLabel: '趋势分析',
    backendBadgeLabel: null,
    language: 'zh' as const,
    thinkingExpanded: false,
    expandedCategories: new Set<string>(),
    onToggleCategory: vi.fn(),
    onCollapseThinking: vi.fn(),
    onFollowUp: vi.fn(),
    clarification: null,
    clarificationAutoConfirmEnabled: false,
    copied: false,
    copyLabel: '复制',
    exportLabel: '导出',
    onCopy: vi.fn(),
    onExport: vi.fn(),
    onRequestComposerFocus: vi.fn(),
    onClarificationDecision: vi.fn(),
  };
}

describe('ChatMessageItem memoization (T40)', () => {
  beforeEach(() => {
    renderSpy.mockClear();
  });

  it('does not re-render an existing message when the parent re-renders with unchanged props', () => {
    const props = buildProps();

    function Harness() {
      const [draft, setDraft] = useState('');
      return (
        <div>
          <input
            aria-label="composer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <ChatMessageItem {...props} />
        </div>
      );
    }

    render(<Harness />);
    expect(renderSpy).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText('composer'), { target: { value: '分' } });
    fireEvent.change(screen.getByLabelText('composer'), { target: { value: '分析' } });

    // Typing only re-renders the harness; the memoized message subtree is skipped.
    expect(renderSpy).toHaveBeenCalledTimes(1);
  });

  it('re-renders when its own props change', () => {
    const props = buildProps();
    const { rerender } = render(<ChatMessageItem {...props} />);
    expect(renderSpy).toHaveBeenCalledTimes(1);

    rerender(<ChatMessageItem {...props} copied />);

    expect(renderSpy).toHaveBeenCalledTimes(2);
  });

  it('shows one summarized row per tool call instead of start/done chatter', () => {
    const props = buildProps();
    props.message = {
      id: 'msg-2',
      role: 'assistant',
      content: '茅台趋势向上',
      thinkingSteps: [
        { type: 'thinking', message: '正在制定分析路径...' },
        { type: 'tool_start', tool: 'realtime_quote', call_id: 'c1', display_label: '实时行情' },
        {
          type: 'tool_done',
          tool: 'realtime_quote',
          call_id: 'c1',
          display_label: '实时行情',
          success: true,
          duration: 0.3,
          outcome: 'success',
        },
        { type: 'thinking', message: '「实时行情」已完成，继续深入分析...' },
      ],
    };
    props.thinkingExpanded = true;
    props.expandedCategories = new Set(['msg-2:quote']);

    render(<ChatMessageItem {...props} />);

    // The merged tool row is shown once, and the repetitive thinking chatter
    // is no longer rendered in the expanded details.
    expect(screen.getAllByText(/实时行情/)).toHaveLength(1);
    expect(screen.queryByText(/继续深入分析/)).not.toBeInTheDocument();
    expect(screen.getByText('1 次工具调用')).toBeInTheDocument();
  });
});
