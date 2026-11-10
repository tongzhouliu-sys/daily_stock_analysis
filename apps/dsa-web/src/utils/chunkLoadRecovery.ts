const CHUNK_RELOAD_STORAGE_KEY = 'dsa.chunkLoadReloadAt';
const CHUNK_RELOAD_WINDOW_MS = 30_000;

const CHUNK_ERROR_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /failed to load module script/i,
  // A CDN/SPA fallback can answer a hashed JS request with text/html. Browsers
  // report that as a MIME mismatch rather than a dynamic import failure.
  /expected a javascript module script/i,
  /mime type of ["']?text\/(?:html|plain)/i,
  /unable to preload css/i,
  /loading (?:css )?chunk \S+ failed/i,
];

type ChunkRecoveryWindow = Pick<Window, 'addEventListener' | 'removeEventListener'>;

type ChunkReloadRecoveryOptions = {
  now?: number;
  storage?: Storage | null;
  reload?: () => void;
  /** Explicit user-triggered retry: bypass the anti-loop guard and reload now. */
  force?: boolean;
};

/**
 * A deploy replaces hashed route chunks. Tabs still running the previous
 * index can therefore fail to import a route that no longer exists.
 */
export function isChunkLoadError(error: unknown): boolean {
  if (error instanceof Error) {
    if (error.name === 'ChunkLoadError' || CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(error.message))) {
      return true;
    }
    return error.cause !== undefined && error.cause !== error && isChunkLoadError(error.cause);
  }
  if (typeof error === 'string') {
    return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(error));
  }
  return false;
}

export function shouldAttemptChunkReload(now: number, lastAttemptAt: number | null): boolean {
  return lastAttemptAt === null || now - lastAttemptAt >= CHUNK_RELOAD_WINDOW_MS;
}

function getSessionStorage(): Storage | null {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function getLastReloadAt(storage: Storage): number | null | undefined {
  try {
    const raw = storage.getItem(CHUNK_RELOAD_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return undefined;
  }
}

/**
 * Reload once to pick up the current index and its chunk map. The timestamp
 * guard prevents a reload loop when the deployed build itself is broken.
 */
export function recoverFromChunkLoadError(options: ChunkReloadRecoveryOptions = {}): boolean {
  const reload = options.reload ?? (() => window.location.reload());

  // The user clicked "reload" on the error page. Do not consume the guarded
  // automatic attempt and do not depend on sessionStorage availability.
  if (options.force) {
    reload();
    return true;
  }

  const storage = options.storage === undefined ? getSessionStorage() : options.storage;
  if (!storage) {
    return false;
  }

  const now = options.now ?? Date.now();
  const lastReloadAt = getLastReloadAt(storage);
  if (lastReloadAt === undefined || !shouldAttemptChunkReload(now, lastReloadAt)) {
    return false;
  }

  try {
    storage.setItem(CHUNK_RELOAD_STORAGE_KEY, String(now));
  } catch {
    return false;
  }

  reload();
  return true;
}

/**
 * Vite emits `vite:preloadError` for failed module preloads, including cases
 * outside the React render tree. Rejections from plain `import()` calls are
 * handled by the same guarded reload.
 */
export function installChunkLoadRecovery({
  targetWindow,
  recover = recoverFromChunkLoadError,
}: {
  targetWindow: ChunkRecoveryWindow;
  recover?: () => boolean;
}): () => void {
  const onPreloadError = (event: Event) => {
    if (recover()) {
      event.preventDefault();
    }
  };
  const onUnhandledRejection = (event: PromiseRejectionEvent) => {
    if (isChunkLoadError(event.reason)) {
      recover();
    }
  };

  targetWindow.addEventListener('vite:preloadError', onPreloadError);
  targetWindow.addEventListener('unhandledrejection', onUnhandledRejection);

  return () => {
    targetWindow.removeEventListener('vite:preloadError', onPreloadError);
    targetWindow.removeEventListener('unhandledrejection', onUnhandledRejection);
  };
}
