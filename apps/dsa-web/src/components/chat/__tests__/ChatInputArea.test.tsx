import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ChatInputArea from '../ChatInputArea';

const t = ((key: string) => key) as unknown as Parameters<typeof ChatInputArea>[0]['t'];

function renderInput(overrides: Partial<Parameters<typeof ChatInputArea>[0]> = {}) {
  const onSend = vi.fn();
  const onInputChange = vi.fn();
  const props = {
    t,
    composerRef: createRef<HTMLTextAreaElement>(),
    input: '分析茅台',
    onInputChange,
    onSend,
    onStop: vi.fn(),
    placeholder: '请输入',
    disabled: false,
    loading: false,
    advancedOptionsOpen: false,
    onToggleAdvancedOptions: vi.fn(),
    selectedSkillSummary: '通用分析',
    skills: [],
    selectedSkillIds: [] as string[],
    skillLimitReached: false,
    showSkillDesc: null as string | null,
    onShowSkillDesc: vi.fn(),
    onToggleSkill: vi.fn(),
    onClearSkills: vi.fn(),
    contextCompressionEnabled: false,
    contextCompressionLoaded: true,
    contextCompressionSaving: false,
    contextCompressionError: null as string | null,
    onToggleContextCompression: vi.fn(),
    activeStockCode: null as string | null,
    canToggleWatchlist: false,
    watchlistBusy: false,
    inWatchlist: false,
    onToggleWatchlist: vi.fn(),
    watchlistMessage: null as string | null,
    objectStatusText: '',
    ...overrides,
  };
  const result = render(<ChatInputArea {...props} />);
  return { ...result, onSend, onInputChange };
}

describe('ChatInputArea Enter handling (T40)', () => {
  it('sends on Enter and keeps Shift+Enter as a newline', () => {
    const { onSend } = renderInput();
    const textarea = screen.getByPlaceholderText('请输入');

    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('does not send while an IME composition is active', () => {
    const { onSend } = renderInput();
    const textarea = screen.getByPlaceholderText('请输入');

    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true });
    expect(onSend).not.toHaveBeenCalled();

    // Some IME implementations only report the legacy keyCode 229.
    fireEvent.keyDown(textarea, { key: 'Enter', keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledTimes(1);
  });
});
