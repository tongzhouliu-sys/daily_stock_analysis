import { useEffect, useState } from 'react';
import { elapsedSecondsSince } from '../utils/runStatus';

/**
 * Whole seconds elapsed since `startedAt`, refreshed once per second.
 *
 * Deliberately a leaf hook: pages re-render on every task event, and a page
 * that also ticked once per second would re-render the whole report tree.
 */
export function useElapsedSeconds(startedAt?: string | number | null): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (startedAt === null || startedAt === undefined) {
      return undefined;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  return elapsedSecondsSince(startedAt, now);
}

export default useElapsedSeconds;
