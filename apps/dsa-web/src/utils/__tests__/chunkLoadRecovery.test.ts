import { describe, expect, it, vi } from 'vitest';
import {
  installChunkLoadRecovery,
  isChunkLoadError,
  recoverFromChunkLoadError,
  shouldAttemptChunkReload,
} from '../chunkLoadRecovery';

function createMemoryStorage(initial: Record<string, string> = {}): Storage {
  const values = new Map(Object.entries(initial));

  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    removeItem: (key: string) => {
      values.delete(key);
    },
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

describe('isChunkLoadError', () => {
  it.each([
    'Failed to fetch dynamically imported module: https://example.com/assets/HomePage-abc.js',
    'error loading dynamically imported module: https://example.com/assets/HomePage-abc.js',
    'Importing a module script failed.',
    'Unable to preload CSS for /assets/HomePage-abc.css',
    'Loading chunk 12 failed.',
    'Failed to load module script: Expected a JavaScript module script but the server responded with a MIME type of "text/html"',
    'Expected a JavaScript module script but the server responded with a MIME type of "text/plain"',
  ])('recognizes stale chunk errors from browsers: %s', (message) => {
    expect(isChunkLoadError(new Error(message))).toBe(true);
  });

  it('recognizes named ChunkLoadError and nested causes', () => {
    const namedError = new Error('asset unavailable');
    namedError.name = 'ChunkLoadError';

    expect(isChunkLoadError(namedError)).toBe(true);
    expect(isChunkLoadError(new Error('import failed', {
      cause: new Error('Failed to fetch dynamically imported module: /assets/page.js'),
    }))).toBe(true);
  });

  it('does not treat ordinary render errors as chunk failures', () => {
    expect(isChunkLoadError(new Error('chunk load failed'))).toBe(false);
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
  });
});

describe('recoverFromChunkLoadError', () => {
  it('reloads once and suppresses repeated reloads inside the guard window', () => {
    const storage = createMemoryStorage();
    const reload = vi.fn();

    expect(recoverFromChunkLoadError({ now: 1_000, storage, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(storage.getItem('dsa.chunkLoadReloadAt')).toBe('1000');

    expect(recoverFromChunkLoadError({ now: 1_000 + 29_999, storage, reload })).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);

    expect(recoverFromChunkLoadError({ now: 1_000 + 30_000, storage, reload })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('forces a reload for an explicit user retry even inside the guard window', () => {
    const storage = createMemoryStorage({ 'dsa.chunkLoadReloadAt': '1000' });
    const reload = vi.fn();

    expect(recoverFromChunkLoadError({
      now: 1_500,
      storage,
      reload,
      force: true,
    })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    // A manual retry must not consume the guarded automatic attempt.
    expect(storage.getItem('dsa.chunkLoadReloadAt')).toBe('1000');
  });

  it('forces a reload even when storage is unavailable', () => {
    const reload = vi.fn();

    expect(recoverFromChunkLoadError({ storage: null, reload, force: true })).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not reload when the guard state cannot be read or written', () => {
    const reload = vi.fn();
    const brokenStorage = {
      ...createMemoryStorage(),
      getItem: () => {
        throw new Error('storage blocked');
      },
      setItem: () => {
        throw new Error('storage blocked');
      },
    } as Storage;

    expect(recoverFromChunkLoadError({ now: 1_000, storage: brokenStorage, reload })).toBe(false);
    expect(recoverFromChunkLoadError({ now: 1_000, storage: null, reload })).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('shouldAttemptChunkReload', () => {
  it('allows the first attempt and attempts after the window', () => {
    expect(shouldAttemptChunkReload(1_000, null)).toBe(true);
    expect(shouldAttemptChunkReload(1_000, 500)).toBe(false);
    expect(shouldAttemptChunkReload(2_000, 1_000)).toBe(false);
    expect(shouldAttemptChunkReload(31_000, 1_000)).toBe(true);
  });
});

describe('installChunkLoadRecovery', () => {
  it('handles Vite preload failures and removes its listeners on cleanup', () => {
    const targetWindow = new EventTarget() as unknown as Window;
    const recover = vi.fn(() => true);
    const uninstall = installChunkLoadRecovery({ targetWindow, recover });

    const preloadError = new Event('vite:preloadError', { cancelable: true });
    targetWindow.dispatchEvent(preloadError);

    expect(recover).toHaveBeenCalledTimes(1);
    expect(preloadError.defaultPrevented).toBe(true);

    uninstall();
    targetWindow.dispatchEvent(new Event('vite:preloadError'));
    expect(recover).toHaveBeenCalledTimes(1);
  });

  it('only recovers unhandled rejections that look like chunk failures', () => {
    const targetWindow = new EventTarget() as unknown as Window;
    const recover = vi.fn(() => false);
    const uninstall = installChunkLoadRecovery({ targetWindow, recover });

    const chunkRejection = Object.assign(new Event('unhandledrejection'), {
      reason: new Error('Failed to fetch dynamically imported module: /assets/page.js'),
    });
    const renderRejection = Object.assign(new Event('unhandledrejection'), {
      reason: new Error('Cannot read properties of undefined'),
    });

    targetWindow.dispatchEvent(renderRejection);
    expect(recover).not.toHaveBeenCalled();

    targetWindow.dispatchEvent(chunkRejection);
    expect(recover).toHaveBeenCalledTimes(1);

    uninstall();
  });
});
