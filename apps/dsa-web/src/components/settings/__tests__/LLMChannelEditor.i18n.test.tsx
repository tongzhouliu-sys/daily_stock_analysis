import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { formatUiText, UI_TEXT, type UiTextKey } from '../../../i18n/uiText';

vi.mock('../../../contexts/UiLanguageContext', () => ({
  useUiLanguage: () => ({
    language: 'en',
    setLanguage: () => undefined,
    t: (key: UiTextKey, params?: Record<string, string | number>) => formatUiText(UI_TEXT.en[key], params),
  }),
}));

vi.mock('../../../api/systemConfig', () => ({
  systemConfigApi: {
    update: vi.fn(),
    testLLMChannel: vi.fn(),
    discoverLLMChannelModels: vi.fn(),
  },
}));

import { LLMChannelEditor } from '../LLMChannelEditor';

const CJK_PATTERN = /[\u4e00-\u9fff]/;

const items = [
  { key: 'LLM_CHANNELS', value: 'deepseek,anspire' },
  { key: 'LLM_DEEPSEEK_PROTOCOL', value: 'deepseek' },
  { key: 'LLM_DEEPSEEK_BASE_URL', value: 'https://api.deepseek.com' },
  { key: 'LLM_DEEPSEEK_ENABLED', value: 'true' },
  { key: 'LLM_DEEPSEEK_API_KEY', value: 'secret-key' },
  { key: 'LLM_DEEPSEEK_MODELS', value: 'deepseek-v4-flash' },
  { key: 'LLM_ANSPIRE_PROTOCOL', value: 'openai' },
  { key: 'LLM_ANSPIRE_BASE_URL', value: 'https://open-gateway.anspire.cn/v6' },
  { key: 'LLM_ANSPIRE_ENABLED', value: 'true' },
  { key: 'LLM_ANSPIRE_API_KEY', value: 'anspire-key' },
  { key: 'LLM_ANSPIRE_MODELS', value: 'Doubao-Seed-2.0-lite' },
  { key: 'LITELLM_MODEL', value: 'deepseek/deepseek-v4-flash' },
];

describe('LLMChannelEditor English mode', () => {
  it('renders collapsed and expanded channel details without Chinese text', () => {
    const { container } = render(
      <LLMChannelEditor
        items={items}
        configVersion="v1"
        maskToken="******"
        onSaved={vi.fn()}
      />,
    );

    expect(container.textContent || '').not.toMatch(CJK_PATTERN);

    // Expand the provider template with capabilities, hints and official sources.
    fireEvent.click(screen.getAllByText('Anspire Open').at(-1)!);
    expect(container.textContent || '').not.toMatch(CJK_PATTERN);

    // Expand the runtime capability panel and provider template details.
    fireEvent.click(screen.getAllByText('DeepSeek official').at(-1)!);
    expect(container.textContent || '').not.toMatch(CJK_PATTERN);
    expect(screen.getAllByText('Configuration reference').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Runtime capability checks (optional)').length).toBeGreaterThan(0);
  });
});
