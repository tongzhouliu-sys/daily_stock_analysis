import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StockClarificationCard, {
  STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS,
} from '../StockClarificationCard';
import type {
  StockClarification,
  StockClarificationIdentity,
} from '../../../stores/agentChatStore';

const smic: StockClarificationIdentity = {
  stock_code: '688981',
  stock_name: '中芯国际',
  canonical_id: 'sh688981',
  asset_type: 'stock',
};
const smicHk: StockClarificationIdentity = {
  stock_code: 'HK00981',
  stock_name: '中芯国际',
  canonical_id: 'hk00981',
  asset_type: 'stock',
};

const switchClarification: StockClarification = {
  reason: 'ambiguous_name',
  action: 'switch',
  current: null,
  candidates: [smic, smicHk],
  recommended: smic,
};

function renderCard(overrides: {
  clarification?: StockClarification;
  autoConfirmEnabled?: boolean;
  onConfirm?: (decision: 'switch' | 'keep', target: StockClarificationIdentity | null) => void;
  onRequestInput?: () => void;
} = {}) {
  const onConfirm = overrides.onConfirm ?? vi.fn();
  const onRequestInput = overrides.onRequestInput ?? vi.fn();
  const result = render(
    <StockClarificationCard
      messageId="message-1"
      clarification={overrides.clarification ?? switchClarification}
      autoConfirmEnabled={overrides.autoConfirmEnabled ?? true}
      onConfirm={onConfirm}
      onRequestInput={onRequestInput}
    />,
  );
  return { onConfirm, onRequestInput, ...result };
}

const advance = (ms: number) => act(() => {
  vi.advanceTimersByTime(ms);
});

const resolveTarget = () => {
  for (let step = 0; step < 4; step += 1) {
    advance(400);
  }
  advance(300);
};

describe('StockClarificationCard', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('resolves the target before showing the default reply timer', () => {
    renderCard();

    expect(screen.getByText('先确认要分析的对象')).toBeInTheDocument();
    expect(screen.queryByText(/默认回复/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /解析中/ })).toBeDisabled();

    resolveTarget();

    expect(screen.getByText('已锁定新的分析标的')).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`${STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS}s 后自动发送`)))
      .toBeInTheDocument();
    expect(screen.getAllByText('中芯国际').length).toBeGreaterThan(0);
    expect(screen.getAllByText('688981').length).toBeGreaterThan(0);
  });

  it('auto-confirms the recommended switch after the 10 second default reply', () => {
    const { onConfirm } = renderCard();

    resolveTarget();
    expect(onConfirm).not.toHaveBeenCalled();

    for (let second = 0; second < 10; second += 1) {
      advance(1000);
    }

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm).toHaveBeenCalledWith('switch', smic);
  });

  it('never auto-sends when auto confirm is disabled', () => {
    const { onConfirm } = renderCard({ autoConfirmEnabled: false });

    advance(20_000);

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByText(/默认回复/)).not.toBeInTheDocument();
  });

  it('sends the current target when the user keeps it', () => {
    const { onConfirm } = renderCard({
      clarification: {
        reason: 'missing_object',
        action: 'keep',
        current: smic,
        candidates: [],
        recommended: null,
      },
    });

    resolveTarget();
    fireEvent.click(screen.getByRole('button', { name: '继续分析' }));

    expect(onConfirm).toHaveBeenCalledWith('keep', null);
  });

  it('lets the user choose another candidate instead of the recommendation', () => {
    const { onConfirm } = renderCard({
      clarification: {
        reason: 'ambiguous_name',
        action: 'ask',
        current: null,
        candidates: [smic, smicHk],
        recommended: null,
      },
    });

    resolveTarget();
    fireEvent.click(screen.getByRole('button', { name: /HK00981/ }));

    expect(onConfirm).toHaveBeenCalledWith('switch', smicHk);
  });

  it('pauses the default reply when the user asks to type a code', () => {
    const { onConfirm, onRequestInput } = renderCard();

    resolveTarget();
    fireEvent.click(screen.getByRole('button', { name: '输入股票代码' }));
    expect(onRequestInput).toHaveBeenCalledTimes(1);

    for (let second = 0; second < 20; second += 1) {
      advance(1000);
    }

    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByText(/默认回复/)).not.toBeInTheDocument();
  });
});
