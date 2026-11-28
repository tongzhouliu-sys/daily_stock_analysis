import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChatUnderstandingCard } from '../ChatUnderstandingCard';
import type { IntakeDraft } from '../../../api/agentIntake';

const baseDraft: IntakeDraft = {
  intake_id: 'intake-1',
  schema_version: 2,
  session_id: 'session-1',
  session_generation: 'gen-1',
  base_state_version: 0,
  revision: 1,
  original_message: '我持有茅台被套了，长期怎么看',
  questions: [
    {
      id: 'position',
      dimension: 'position',
      type: 'single',
      title: '持仓',
      required: false,
      options: [
        { id: 'held_control_drawdown', label: '已持有，关注回撤' },
        { id: 'not_held', label: '尚未持有' },
      ],
    },
    {
      id: 'horizon',
      dimension: 'horizon',
      type: 'single',
      title: '周期',
      required: false,
      options: [{ id: 'long', label: '6 个月以上' }],
    },
  ],
  answers: [
    { question_id: 'position', option_ids: ['held_control_drawdown'], source: 'explicit_message' },
    { question_id: 'horizon', option_ids: ['long'], source: 'explicit_message' },
  ],
  status: 'confirmed',
  resolution: {
    status: 'resolved',
    scope: 'stock',
    identity: {
      stock_code: '600519',
      stock_name: '贵州茅台',
      canonical_id: '600519.SH',
      asset_type: 'stock',
      market: 'CN',
      market_label: 'A股',
    },
    candidates: [],
    recommended_option_id: null,
    reason: 'unique_object',
  },
  brief_preview: {
    brief_version: 1,
    object_scope: '600519',
    position: 'held_control_drawdown',
    horizon: 'long',
    sources: { position: 'explicit_message', horizon: 'explicit_message' },
    evidence: { position: '被套', horizon: '长期' },
  },
  plan: { auto_start: true, blocking_reason: null, question_count: 0 },
};

describe('ChatUnderstandingCard', () => {
  it('shows what the server understood without asking a question', () => {
    render(
      <ChatUnderstandingCard draft={baseDraft} onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );

    expect(screen.getByTestId('chat-understanding-object')).toHaveTextContent('贵州茅台');
    expect(screen.getByText('已持有，关注回撤')).toBeInTheDocument();
    expect(screen.getByText('6 个月以上')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-understanding-confirm')).not.toBeInTheDocument();
    expect(screen.getByTestId('chat-understanding-start')).toBeInTheDocument();
  });

  it('asks one question for an ambiguous name and confirms with the picked object', () => {
    const onConfirm = vi.fn();
    const draft: IntakeDraft = {
      ...baseDraft,
      status: 'pending',
      resolution: {
        status: 'ambiguous',
        scope: 'stock',
        identity: null,
        candidates: [
          {
            stock_code: '688981',
            stock_name: '中芯国际',
            canonical_id: '688981.SH',
            asset_type: 'stock',
            market: 'CN',
            market_label: 'A股',
          },
          {
            stock_code: '00981',
            stock_name: '中芯国际',
            canonical_id: '00981.HK',
            asset_type: 'stock',
            market: 'HK',
            market_label: '港股',
          },
        ],
        recommended_option_id: '688981.SH',
        reason: 'ambiguous_object',
      },
      plan: { auto_start: false, blocking_reason: 'ambiguous', question_count: 1 },
    };

    render(<ChatUnderstandingCard draft={draft} onConfirm={onConfirm} onCancel={vi.fn()} />);

    const options = screen.getAllByRole('radio');
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(options[1]);
    fireEvent.click(screen.getByTestId('chat-understanding-confirm'));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    const answers = onConfirm.mock.calls[0][0];
    expect(answers.find((answer: { question_id: string }) => answer.question_id === 'object'))
      .toEqual({ question_id: 'object', option_ids: ['00981.HK'], source: 'user_choice' });
  });

  it('offers search and edit for a missing object', () => {
    const draft: IntakeDraft = {
      ...baseDraft,
      status: 'pending',
      resolution: {
        status: 'missing',
        scope: 'stock',
        identity: null,
        candidates: [],
        recommended_option_id: null,
        reason: 'missing_object',
      },
      plan: { auto_start: false, blocking_reason: 'missing', question_count: 1 },
    };

    render(<ChatUnderstandingCard draft={draft} onConfirm={vi.fn()} onCancel={vi.fn()} />);

    expect(screen.getByText('没在问题里找到具体股票')).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText('名称、代码或拼音首字母，如 茅台 / 600519 / gzmt / AAPL').length).toBeGreaterThan(0);
    expect(screen.getByText('修改问题')).toBeInTheDocument();
    expect(screen.getByTestId('chat-understanding-confirm')).toBeDisabled();
  });

  it('lets the user remove an inferred chip before confirming', () => {
    render(
      <ChatUnderstandingCard
        draft={{ ...baseDraft, status: 'pending', plan: { auto_start: false, blocking_reason: 'ambiguous', question_count: 1 } }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByLabelText('移除 6 个月以上'));
    expect(screen.queryByText('6 个月以上')).not.toBeInTheDocument();
  });
});
