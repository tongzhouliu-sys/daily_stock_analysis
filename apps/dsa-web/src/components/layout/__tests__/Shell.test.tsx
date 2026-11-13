import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../../theme/ThemeProvider';
import { Shell } from '../Shell';

const mockLogout = vi.fn().mockResolvedValue(undefined);

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: true,
    logout: mockLogout,
  }),
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: true }),
}));

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

describe('Shell', () => {
  afterEach(() => {
    delete (window as { dsaDesktop?: unknown }).dsaDesktop;
    localStorage.clear();
  });

  it('collapses the desktop navigation and persists the preference', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    const shell = document.querySelector('.research-shell');
    expect(shell).toHaveAttribute('data-nav-collapsed', 'false');

    fireEvent.click(screen.getByRole('button', { name: '折叠侧边栏' }));

    expect(shell).toHaveAttribute('data-nav-collapsed', 'true');
    expect(localStorage.getItem('dsa_shell_nav_collapsed')).toBe('true');
    expect(screen.getByRole('button', { name: '展开侧边栏' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '展开侧边栏' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('renders theme toggle, language toggle and logout button in header', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    expect(screen.getByRole('button', { name: '切换主题' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换界面语言' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '退出' })).toBeInTheDocument();
  });

  it('shows about icon with copyright tooltip on hover', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    const aboutButton = screen.getByRole('button', { name: '关于我们' });
    expect(aboutButton).toBeInTheDocument();

    fireEvent.mouseEnter(aboutButton);
    expect(screen.getByText('版权所有 2026@RHCLOUD PTE. LTD.')).toBeInTheDocument();
  });

  it('hides the desktop update entry outside Electron runtime', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    expect(screen.queryByRole('button', { name: '桌面端更新' })).not.toBeInTheDocument();
  });

  it('shows the desktop update entry in Electron runtime', async () => {
    (window as { dsaDesktop?: unknown }).dsaDesktop = {
      version: '3.30.0',
      getUpdateState: vi.fn().mockResolvedValue({ status: 'idle', currentVersion: '3.30.0' }),
      checkForUpdates: vi.fn(),
      openReleasePage: vi.fn(),
      onUpdateStateChange: vi.fn(() => () => undefined),
    };

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    expect(await screen.findByRole('button', { name: '桌面端更新' })).toBeInTheDocument();
  });

  it('shows a confirmation dialog before logout', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: '退出' }));

    expect(await screen.findByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));
    expect(mockLogout).toHaveBeenCalled();
  });
});
