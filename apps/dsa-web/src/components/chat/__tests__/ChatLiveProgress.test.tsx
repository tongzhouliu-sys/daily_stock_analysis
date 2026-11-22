import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ChatLiveProgress from '../ChatLiveProgress';
import type { ProgressStep } from '../../../stores/agentChatStore';

const longStreamingAnswer = [
  '## 结论',
  '贵州茅台当前处于高位震荡阶段。'.repeat(20),
  '## 一、趋势结构',
  '日线 MA20 走平，MA60 仍保持上行。'.repeat(20),
  '## 二、操作建议',
  '跌破 1480 减仓，止损位放在 1450。'.repeat(20),
  '## 后续建议',
  '- 茅台近期的北向资金流向如何？',
].join('\n\n');

const makeSteps = (count: number): ProgressStep[] => Array.from({ length: count }, (_, index) => ({
  type: 'tool_done',
  tool: `tool_${index}`,
  call_id: `call-${index}`,
  display_label: `步骤 ${index + 1}`,
  success: true,
  outcome: 'success',
  duration: 0.5 + index,
}));

describe('ChatLiveProgress', () => {
  it('streams the answer without long-answer controls that would disappear later', () => {
    render(
      <ChatLiveProgress steps={[]} streamingContent={longStreamingAnswer} language="zh" />,
    );

    expect(screen.getByTestId('chat-live-progress')).toBeInTheDocument();
    // ToC / collapse only belong to the finished message; rendering them
    // mid-stream makes the panel jump when the turn completes.
    expect(screen.queryByText('本页目录')).not.toBeInTheDocument();
    expect(screen.queryByText('展开全文')).not.toBeInTheDocument();
  });

  it('caps the expanded step list and lets the user collapse it again', () => {
    const { container } = render(
      <ChatLiveProgress steps={makeSteps(9)} streamingContent={null} language="zh" />,
    );

    const toggle = screen.getByRole('button', { name: /展开更早的 3 步/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('步骤 1')).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(screen.getByText('步骤 1')).toBeInTheDocument();
    const stepsBox = container.querySelector('.chat-live-steps');
    expect(stepsBox).toHaveClass('is-expanded');

    const collapse = screen.getByRole('button', { name: /收起更早步骤/ });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(collapse);

    expect(screen.queryByText('步骤 1')).not.toBeInTheDocument();
    expect(container.querySelector('.chat-live-steps')).not.toHaveClass('is-expanded');
  });
});
