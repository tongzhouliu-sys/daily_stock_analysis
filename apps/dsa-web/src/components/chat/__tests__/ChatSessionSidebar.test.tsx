import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ChatSessionSidebar, { type ChatSidebarGroup } from '../ChatSessionSidebar';
import { UI_TEXT, type UiTextKey, type UiTextParams } from '../../../i18n/uiText';

const t = ((key: UiTextKey, params?: UiTextParams) => {
  const template = UI_TEXT.zh[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name: string) => String(params?.[name] ?? ''));
}) as Parameters<typeof ChatSessionSidebar>[0]['t'];

const groups: ChatSidebarGroup[] = [
  {
    key: 'pinned',
    label: '置顶',
    items: [
      { session_id: 's1', title: '贵州茅台走势', is_pinned: true, message_count: 6, last_active: '2026-03-15T09:05:00Z' },
    ],
  },
  {
    key: 'today',
    label: '今天',
    items: [
      { session_id: 's2', title: '比亚迪趋势', is_pinned: false, message_count: 2, last_active: '2026-03-15T08:00:00Z' },
    ],
  },
];

function renderSidebar(overrides: Partial<Parameters<typeof ChatSessionSidebar>[0]> = {}) {
  const props = {
    t,
    language: 'zh' as const,
    sessionGroups: groups,
    sessionsCount: 2,
    sessionsLoading: false,
    sessionId: 's1',
    menuSessionId: null,
    editingSessionId: null,
    titleEditorOrigin: null,
    sessionTitle: '',
    titleSaving: false,
    titleError: '',
    onStartNewChat: vi.fn(),
    onToggleSidebar: vi.fn(),
    onSwitchSession: vi.fn(),
    onChangeTitle: vi.fn(),
    onSaveTitle: vi.fn(),
    onCloseTitleEditor: vi.fn(),
    onOpenMenu: vi.fn(),
    ...overrides,
  };
  return render(<ChatSessionSidebar {...props} />);
}

describe('ChatSessionSidebar rows', () => {
  it('shows a second line with the message count so similar titles stay distinct', () => {
    renderSidebar();

    const row = screen.getByRole('button', { name: '切换到对话 贵州茅台走势' });
    expect(row).toHaveTextContent('6 条消息');
    expect(row.querySelector('.chat-session-meta')?.textContent).toContain('6 条消息');
  });

  it('keeps the desktop collapse control and the new-chat entry', () => {
    renderSidebar();

    expect(screen.getByRole('button', { name: '开启新对话' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '折叠历史对话' })).toBeInTheDocument();
  });

  it('renders the search field and scope hint in the phone panel without a collapse control', () => {
    const onSearchChange = vi.fn();
    renderSidebar({
      variant: 'mobile',
      onSearchChange,
      searchScopeHint: '仅搜索最近 50 个对话',
    });

    const search = screen.getByRole('searchbox', { name: '搜索标题' });
    fireEvent.change(search, { target: { value: '茅台' } });

    expect(onSearchChange).toHaveBeenCalledWith('茅台');
    expect(screen.getByText('仅搜索最近 50 个对话')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '折叠历史对话' })).not.toBeInTheDocument();
  });

  it('explains an empty search result instead of pretending there is no history', () => {
    renderSidebar({ variant: 'mobile', sessionGroups: [], sessionsCount: 24 });

    expect(screen.getByText('没有匹配的对话')).toBeInTheDocument();
  });
});
