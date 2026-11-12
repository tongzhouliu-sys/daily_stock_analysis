import { create } from 'zustand';
import { agentApi, decodeChatState, isAbortError, intakeApi } from '../api/agent';
import type {
  ActiveStockContext,
  ActiveReportContext,
  ChatSessionDetail,
  ChatSessionItem,
  ChatStateFields,
  ChatStreamRequest,
  DecodedChatState,
} from '../api/agent';
import type {
  IntakeDraft,
  Answer,
  AnalysisBrief,
  IntakeStatus,
} from '../api/agentIntake';
import {
  createParsedApiError,
  getParsedApiError,
  isApiRequestError,
  isParsedApiError,
  type ParsedApiError,
} from '../api/error';
import { generateUUID } from '../utils/uuid';

const STORAGE_KEY_SESSION = 'dsa_chat_session_id';

export interface StockClarificationIdentity {
  stock_code: string;
  stock_name: string | null;
  canonical_id: string;
  asset_type: 'stock' | 'index';
}

export type StockClarificationAction = 'switch' | 'keep' | 'ask';

export type StockClarificationReason =
  | 'unique_candidate'
  | 'ambiguous_name'
  | 'multiple_objects'
  | 'hint_conflict'
  | 'missing_object';

/**
 * Structured evidence for a stock-object clarification. It is attached only to
 * the live assistant turn that produced the clarification, so reopening a
 * historical session never restarts a countdown or silently switches objects.
 */
export interface StockClarification {
  reason: StockClarificationReason;
  action: StockClarificationAction;
  current: StockClarificationIdentity | null;
  candidates: StockClarificationIdentity[];
  recommended: StockClarificationIdentity | null;
}

export interface ProgressStep {
  type: string;
  step?: number;
  stage?: string;
  tool?: string;
  display_name?: string;
  status?: string;
  success?: boolean;
  duration?: number;
  elapsed?: number;
  timeout?: number;
  remaining?: number;
  minimum?: number;
  reason?: string;
  message?: string;
  content?: string;
  meta?: Record<string, unknown>;
  backend?: string;
  error_code?: string;
  request_id?: string;
  session_id?: string;
  clarification?: StockClarification | null;
  // T1: Enhanced fields for analysis process display
  call_id?: string;
  stage_id?: string;
  parent_stage_id?: string;
  outcome?: 'success' | 'partial' | 'failed' | 'timeout' | 'skipped';
  detail?: Record<string, string | number | boolean>;
  category?: string;
  display_label?: string;
  cached?: boolean;
}

// T4: Domain guard metadata on messages
import type { DomainGuardMeta } from '../api/agent';

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  skills?: string[];
  skill?: string;
  skillNames?: string[];
  skillName?: string;
  thinkingSteps?: ProgressStep[];
  backend?: string;
  // Present only on a live clarification turn. Historical messages rehydrated
  // from the session detail intentionally have no such evidence.
  stockClarification?: StockClarification;
  // T4: Domain guard metadata for non-investment question reminders
  domainGuard?: DomainGuardMeta;
}

export interface StreamMeta {
  skillNames?: string[];
  skillName?: string;
  onAccepted?: (event: StreamAcceptedEvent) => void;
}

export interface StreamAcceptedEvent extends ChatStateFields {
  type: 'accepted';
  backend: 'litellm';
  request_id: string;
  session_id: string;
  session_state?: { selected_skill_ids: string[] | null };
}

type StreamTerminalStatus = 'timeout' | 'stopped' | 'interrupted' | null;

type StreamFailureEvent = {
  type: string;
  success?: boolean;
  content?: string;
  error?: unknown;
  message?: unknown;
  backend?: string;
  error_code?: string;
  clarification?: StockClarification | null;
  assistant_message_id?: string | number | null;
  thinking_steps?: ProgressStep[];
  domain_guard?: DomainGuardMeta;
};

function getFirstMeaningfulStreamError(...candidates: Array<unknown>): unknown {
  for (const candidate of candidates) {
    if (typeof candidate === 'string') {
      if (candidate.trim() !== '') {
        return candidate;
      }
      continue;
    }

    if (candidate != null) {
      return candidate;
    }
  }

  return undefined;
}

function getStreamFailureError(
  event: StreamFailureEvent,
  fallbackMessage: string,
): ParsedApiError {
  if (event.error_code === 'session_state_conflict') {
    return getParsedApiError({ code: event.error_code, message: event.message });
  }
  const parsed = getParsedApiError(
    getFirstMeaningfulStreamError(
      event.error,
      event.message,
      event.content,
      fallbackMessage,
    ),
  );
  return event.error_code ? { ...parsed, code: event.error_code } : parsed;
}

interface AgentChatState {
  messages: Message[];
  selectedSkillIds: string[] | null;
  savedSkillIds: string[] | null;
  activeStockContext: ActiveStockContext | null;
  activeReportContext: ActiveReportContext | null;
  stateContract: 'unbound' | DecodedChatState['contract'];
  sessionGeneration: string | null;
  sessionStateVersion: number | null;
  stateSource: 'detail' | 'accepted' | null;
  sessionEpoch: number;
  instanceBindingEpoch: number;
  detailReadSequence: number;
  skillDraftRevision: number;
  skillDraftDirty: boolean;
  loading: boolean;
  progressSteps: ProgressStep[];
  sessionId: string;
  sessions: ChatSessionItem[];
  sessionsLoading: boolean;
  chatError: ParsedApiError | null;
  currentRoute: string;
  completionBadge: boolean;
  hasInitialLoad: boolean;
  abortController: AbortController | null;
  activeRequestId: string | null;
  terminalStatus: StreamTerminalStatus;
  // T3: Intake state
  intakeDraft: IntakeDraft | null;
  intakeStatus: IntakeStatus | null;
  analysisBrief: AnalysisBrief | null;
  // T6: 流式内容输出（临时文本，完成后由 done.content 替换）
  streamingContent: string | null;
}

interface AgentChatActions {
  setSelectedSkillIds: (skillIds: string[]) => void;
  setCurrentRoute: (path: string) => void;
  clearCompletionBadge: () => void;
  loadSessions: () => Promise<void>;
  loadInitialSession: () => Promise<void>;
  switchSession: (targetSessionId: string) => Promise<void>;
  refreshSession: () => Promise<void>;
  startNewChat: () => void;
  stopStream: () => Promise<void>;
  startStream: (payload: ChatStreamRequest, meta?: StreamMeta) => Promise<void>;
  // T3: The original question stays local until the user confirms the
  // background; only startIntakeAnalysis submits one research request.
  createIntake: async (message, payload, meta) => {
    const { sessionId, sessionGeneration } = get();
    set({ intakeBusy: true, intakeError: null, chatError: null });
    try {
      const response = await intakeApi.create({
        message,
        session_id: sessionId,
        session_generation: sessionGeneration,
        request_id: generateUUID(),
        skills: payload?.skills ?? null,
        context: (payload?.context as Record<string, unknown> | undefined) ?? null,
      });
      set({
        intakeDraft: {
          intake_id: response.intake_id,
          schema_version: response.schema_version,
          session_id: sessionId,
          session_generation: response.session_generation,
          base_state_version: response.base_state_version,
          revision: response.revision,
          original_message: message,
          questions: response.questions,
          answers: response.answers,
          status: response.status,
        },
        intakeStatus: response.status,
        analysisBrief: null,
        intakeSendPayload: payload ?? { message, session_id: sessionId },
        intakeSendMeta: meta ?? null,
        intakeBusy: false,
        intakeError: null,
      });
    } catch (error) {
      const parsed = getParsedApiError(error);
      const capabilityMissing = parsed.status === 404 || parsed.status === 405
        || parsed.status === 501;
      set({
        intakeBusy: false,
        intakeError: capabilityMissing
          ? 'intake_capability_missing' : parsed.message || parsed.title,
        intakeSendPayload: payload ?? get().intakeSendPayload,
        intakeSendMeta: meta ?? get().intakeSendMeta,
      });
      if (capabilityMissing && payload) {
        // An old API has no intake: keep the direct question path working.
        await get().startStream(payload, meta);
      }
    }
  },

  updateIntakeAnswers: async (answers, action) => {
    const { intakeDraft, sessionId } = get();
    if (!intakeDraft) return false;
    set({ intakeBusy: true, intakeError: null, chatError: null });
    try {
      const response = await intakeApi.update(intakeDraft.intake_id, {
        expected_revision: intakeDraft.revision,
        answers,
        action,
        session_id: sessionId,
      });
      set((s) => ({
        intakeDraft: s.intakeDraft ? {
          ...s.intakeDraft,
          revision: response.revision,
          answers,
          status: response.status,
          ...(response.questions ? { questions: response.questions } : {}),
        } : null,
        intakeStatus: response.status,
        analysisBrief: response.brief_preview || s.analysisBrief,
        intakeBusy: false,
      }));
      return true;
    } catch (error) {
      // A failed save must never be reported as confirmed.
      const parsed = getParsedApiError(error);
      set({ intakeBusy: false, intakeError: parsed.message || parsed.title });
      return false;
    }
  },

  refreshIntakeDraft: async () => {
    const { intakeDraft, sessionId } = get();
    if (!intakeDraft) return;
    try {
      const fresh = await intakeApi.get(intakeDraft.intake_id, sessionId);
      set({ intakeDraft: fresh, intakeStatus: fresh.status, intakeError: null });
    } catch (error) {
      const parsed = getParsedApiError(error);
      set({ intakeError: parsed.message || parsed.title });
    }
  },

  startIntakeAnalysis: async () => {
    const { intakeDraft, intakeSendPayload, intakeSendMeta, sessionId } = get();
    if (!intakeDraft
      || (intakeDraft.status !== 'confirmed' && intakeDraft.status !== 'skipped')) return;
    const request: ChatStreamRequest = {
      ...(intakeSendPayload ?? {}),
      message: intakeDraft.original_message,
      session_id: sessionId,
      interaction_version: 1,
      intake_id: intakeDraft.intake_id,
      intake_revision: intakeDraft.revision,
    };
    set({
      intakeDraft: null,
      intakeStatus: null,
      analysisBrief: null,
      intakeSendPayload: null,
      intakeSendMeta: null,
      intakeError: null,
    });
    await get().startStream(request, intakeSendMeta ?? undefined);
  },

  skipIntake: async () => {
    const draft = get().intakeDraft;
    if (!draft) return;
    const saved = await get().updateIntakeAnswers(draft.answers, 'skip');
    if (saved) await get().startIntakeAnalysis();
  },

  clearIntake: () => {
    set({
      intakeDraft: null,
      intakeStatus: null,
      analysisBrief: null,
      intakeSendPayload: null,
      intakeSendMeta: null,
      intakeBusy: false,
      intakeError: null,
    });
  },
  };
});
