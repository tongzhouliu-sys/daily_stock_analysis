import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { SidebarNav } from '../SidebarNav';

const completionBadgeState = { value: true };

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: completionBadgeState.value }),
}));

describe('SidebarNav', () => {
  it('keeps screening directly after chat without requiring configuration', () => {
    render(<MemoryRouter initialEntries={['/']}><SidebarNav /></MemoryRouter>);

    expect(screen.getByRole('link', { name: '选股' })).toHaveAttribute('href', '/screening');
    const hrefs = screen.getAllByRole('link').map((link) => link.getAttribute('href'));
    expect(hrefs.slice(0, 6)).toEqual(['/', '/chat', '/screening', '/scanner', '/portfolio', '/decision-signals']);
  });

  it('shows the shared completion badge only when chat completion is pending', () => {
    completionBadgeState.value = true;

    const { rerender } = render(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('chat-completion-badge')).toBeInTheDocument();
    expect(screen.getByLabelText('问股有新消息')).toBeInTheDocument();

    completionBadgeState.value = false;
    rerender(
      <MemoryRouter initialEntries={['/chat']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId('chat-completion-badge')).not.toBeInTheDocument();
  });

  it('renders the alerts navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/alerts']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const alertsLink = screen.getByRole('link', { name: '告警' });
    expect(alertsLink).toHaveAttribute('href', '/alerts');
    expect(alertsLink).toHaveClass('font-medium');
  });

  it('renders the AI signals navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/decision-signals']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const signalsLink = screen.getByRole('link', { name: 'AI 建议' });
    expect(signalsLink).toHaveAttribute('href', '/decision-signals');
    expect(signalsLink).toHaveClass('font-medium');
  });

  it('renders the data center navigation item and marks it active', () => {
    render(
      <MemoryRouter initialEntries={['/data']}>
        <SidebarNav />
      </MemoryRouter>,
    );

    const link = screen.getByRole('link', { name: '数据中心' });
    expect(link).toHaveAttribute('href', '/data');
    expect(link).toHaveClass('font-medium');
  });

  it('does not render theme toggle or logout button in sidebar', () => {
    render(<MemoryRouter><SidebarNav variant="rail" /></MemoryRouter>);

    expect(screen.queryByRole('button', { name: '退出' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '切换主题' })).not.toBeInTheDocument();
  });
});
