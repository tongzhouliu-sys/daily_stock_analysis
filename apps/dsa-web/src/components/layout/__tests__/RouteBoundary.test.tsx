import { fireEvent, render, screen } from '@testing-library/react';
import { lazy } from 'react';
import type React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RouteOutletBoundary } from '../RouteBoundary';
import { Shell } from '../Shell';

const { recoverFromChunkLoadErrorMock } = vi.hoisted(() => ({
  recoverFromChunkLoadErrorMock: vi.fn(() => false),
}));

vi.mock('../../../utils/chunkLoadRecovery', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../utils/chunkLoadRecovery')>();
  return {
    ...actual,
    recoverFromChunkLoadError: recoverFromChunkLoadErrorMock,
  };
});

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: false,
    logout: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('../../../stores/agentChatStore', () => {
  const state = { completionBadge: false };

  return {
    useAgentChatStore: (selector?: (value: typeof state) => unknown) => (
      selector ? selector(state) : state
    ),
  };
});

beforeEach(() => {
  recoverFromChunkLoadErrorMock.mockReset();
  recoverFromChunkLoadErrorMock.mockReturnValue(false);
});

describe('RouteOutletBoundary', () => {
  it('catches rejected lazy route imports inside the shell and resets on navigation', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const BrokenLazyRoute = lazy(() => (
      Promise.reject(new Error('chunk load failed')) as Promise<{ default: React.ComponentType }>
    ));

    try {
      render(
        <MemoryRouter initialEntries={['/chat']}>
          <Routes>
            <Route
              element={(
                <Shell>
                  <RouteOutletBoundary />
                </Shell>
              )}
            >
              <Route path="/chat" element={<BrokenLazyRoute />} />
              <Route path="/portfolio" element={<div data-testid="portfolio-page">Portfolio</div>} />
            </Route>
          </Routes>
        </MemoryRouter>,
      );

      expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
      expect(await screen.findByRole('heading', { name: '页面加载失败' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '重新加载页面' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '返回首页' })).toBeInTheDocument();
      expect(screen.getByTestId('route-error-detail')).toHaveTextContent('chunk load failed');
      expect(recoverFromChunkLoadErrorMock).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('link', { name: '持仓' }));

      expect(await screen.findByTestId('portfolio-page')).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: '页面加载失败' })).not.toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });

  it('attempts a guarded reload when a route chunk is stale after a deploy', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    recoverFromChunkLoadErrorMock.mockReturnValue(true);
    const StaleLazyRoute = lazy(() => (
      Promise.reject(new Error(
        'Failed to fetch dynamically imported module: https://example.com/assets/StockScreeningPage-old.js',
      )) as Promise<{ default: React.ComponentType }>
    ));

    try {
      render(
        <MemoryRouter initialEntries={['/screening']}>
          <Routes>
            <Route
              element={(
                <Shell>
                  <RouteOutletBoundary />
                </Shell>
              )}
            >
              <Route path="/screening" element={<StaleLazyRoute />} />
            </Route>
          </Routes>
        </MemoryRouter>,
      );

      expect(await screen.findByRole('heading', { name: '页面加载失败' })).toBeInTheDocument();
      expect(recoverFromChunkLoadErrorMock).toHaveBeenCalledTimes(1);
    } finally {
      consoleError.mockRestore();
    }
  });

  it('forces a reload when the user clicks the reload button', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const BrokenLazyRoute = lazy(() => (
      Promise.reject(new Error('Cannot read properties of undefined')) as Promise<{ default: React.ComponentType }>
    ));

    try {
      render(
        <MemoryRouter initialEntries={['/chat']}>
          <Routes>
            <Route
              element={(
                <Shell>
                  <RouteOutletBoundary />
                </Shell>
              )}
            >
              <Route path="/chat" element={<BrokenLazyRoute />} />
            </Route>
          </Routes>
        </MemoryRouter>,
      );

      expect(await screen.findByRole('heading', { name: '页面加载失败' })).toBeInTheDocument();
      expect(recoverFromChunkLoadErrorMock).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: '重新加载页面' }));

      expect(recoverFromChunkLoadErrorMock).toHaveBeenCalledWith({ force: true });
    } finally {
      consoleError.mockRestore();
    }
  });
});
