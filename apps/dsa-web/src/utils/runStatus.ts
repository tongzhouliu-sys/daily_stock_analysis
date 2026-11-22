import type { AnalysisRunStage, TaskInfo } from '../types/analysis';

/**
 * Shared run-state model for the home analysis card and the chat panel.
 *
 * Both surfaces must derive "is this alive / slow / stalled" the same way;
 * thresholds are the only thing that differs between them.
 */
export type RunState =
  | 'queued'
  | 'running'
  | 'slow'
  | 'stalled'
  | 'disconnected'
  | 'done'
  | 'failed'
  | 'cancelled';

export interface RunThresholds {
  slowHintMs: number;
  stallHintMs: number;
  disconnectHintMs: number;
}

/** Home analysis tasks wait on data sources and the model, so hints are looser. */
export const TASK_RUN_THRESHOLDS: RunThresholds = {
  slowHintMs: 20_000,
  stallHintMs: 90_000,
  disconnectHintMs: 5_000,
};

/** A chat turn should show its "this is slow" hint sooner. */
export const CHAT_RUN_THRESHOLDS: RunThresholds = {
  slowHintMs: 15_000,
  stallHintMs: 60_000,
  disconnectHintMs: 5_000,
};

/** Stage ids that appear on the five-step strip (queued/done are states). */
export type StripStage = Exclude<AnalysisRunStage, 'queued' | 'done'>;

/** Server order of the five-step analysis strip. */
export const RUN_STAGE_ORDER: StripStage[] = [
  'market_data',
  'fundamentals',
  'news',
  'generating',
  'finalizing',
];

export const RUN_STAGE_LABEL_KEYS = {
  market_data: 'runStatus.stage.marketData',
  fundamentals: 'runStatus.stage.fundamentals',
  news: 'runStatus.stage.news',
  generating: 'runStatus.stage.generating',
  finalizing: 'runStatus.stage.finalizing',
} as const;

export function stageIndex(stage?: string | null): number {
  if (!stage) return -1;
  return RUN_STAGE_ORDER.indexOf(stage as StripStage);
}

/** The next stage the strip should light up, or -1 when nothing is known. */
export function activeStageIndex(stage?: string | null): number {
  if (stage === 'done') return RUN_STAGE_ORDER.length;
  return stageIndex(stage);
}

export function parseTimestamp(value?: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export interface DeriveRunStateInput {
  status?: TaskInfo['status'] | null;
  /** False only when the client knows the live connection is down. */
  connected?: boolean;
  /** Last business-event timestamp reported by the server. */
  progressUpdatedAt?: string | null;
  /** Client-side last-byte timestamp (chat stream liveness). */
  lastActivityAt?: number | null;
  /** Task or turn start timestamp, used when no progress timestamp exists. */
  startedAt?: number | null;
  now?: number;
  thresholds: RunThresholds;
}

/**
 * Decide which of the shared run states applies right now.
 *
 * Pure on purpose: the card and the chat panel can both unit-test it with a
 * frozen `now` instead of waiting on real timers.
 */
export function deriveRunState(input: DeriveRunStateInput): RunState {
  const { status, connected = true, thresholds } = input;
  if (status === 'completed') return 'done';
  if (status === 'failed') return 'failed';
  if (status === 'cancelled') return 'cancelled';
  if (status === 'pending') return 'queued';

  if (!connected) return 'disconnected';

  const now = input.now ?? Date.now();
  const reference =
    input.lastActivityAt
    ?? parseTimestamp(input.progressUpdatedAt)
    ?? input.startedAt
    ?? null;
  if (reference === null) return 'running';

  const silence = now - reference;
  if (silence >= thresholds.stallHintMs) return 'stalled';
  if (silence >= thresholds.slowHintMs) return 'slow';
  return 'running';
}

/** Formats an elapsed duration as mm:ss (hours roll into minutes). */
export function formatElapsed(totalSeconds: number): string {
  const safe = Number.isFinite(totalSeconds) && totalSeconds > 0 ? Math.floor(totalSeconds) : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function elapsedSecondsSince(startedAt?: string | number | null, now = Date.now()): number {
  const start = typeof startedAt === 'number' ? startedAt : parseTimestamp(startedAt);
  if (start === null) return 0;
  return Math.max(0, Math.floor((now - start) / 1000));
}
