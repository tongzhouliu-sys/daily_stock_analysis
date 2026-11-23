import { useCallback, useSyncExternalStore } from 'react';

/**
 * Compact chat layout breakpoint. Mirrors `@media (max-width: 47.999rem)` in
 * `research.css`; keep the two in sync.
 */
export const COMPACT_LAYOUT_QUERY = '(max-width: 47.999rem)';

/**
 * Touch-first devices: no hover and a coarse pointer. Used for behaviour that
 * depends on the input device rather than the viewport width (Enter key role,
 * always-visible message actions, 16px inputs against iOS focus zoom).
 */
export const TOUCH_LAYOUT_QUERY = '(hover: none) and (pointer: coarse)';

const getMediaQueryList = (query: string): MediaQueryList | null => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  return window.matchMedia(query);
};

/** Subscribe to one media query. Returns false where `matchMedia` is absent. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback((onStoreChange: () => void) => {
    const list = getMediaQueryList(query);
    if (!list) return () => undefined;
    list.addEventListener?.('change', onStoreChange);
    return () => list.removeEventListener?.('change', onStoreChange);
  }, [query]);

  const getSnapshot = useCallback(() => getMediaQueryList(query)?.matches ?? false, [query]);

  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}

export function useCompactLayout(): boolean {
  return useMediaQuery(COMPACT_LAYOUT_QUERY);
}

export function useTouchLayout(): boolean {
  return useMediaQuery(TOUCH_LAYOUT_QUERY);
}
