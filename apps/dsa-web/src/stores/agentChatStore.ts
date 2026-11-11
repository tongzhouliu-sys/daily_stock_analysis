import { create } from 'zustand';
import { agentApi, decodeChatState, isAbortError } from '../api/agent';
import type {
  ActiveStockContext,
  ActiveReportContext,
  ChatSessionDetail,
  ChatSessionItem,
  ChatStateFields,
  ChatStreamRequest,
  DecodedChatState,
} from '../api/agent';
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

type StreamTerminalStatus = 'timeout' | 'stopped' | null;

type StreamFailureEvent = {
  type: string;
  success?: boolean;
  content?: string;
  error?: unknown;
  message?: unknown;
  backend?: string;
  error_code?: string;
  clarification?: StockClarification | null;
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
}

const getInitialSessionId = (): string =>
  typeof localStorage !== 'undefined'
    ? localStorage.getItem(STORAGE_KEY_SESSION) || generateUUID()
    : generateUUID();

const emptySessionState = {
  activeStockContext: null, activeReportContext: null, savedSkillIds: null, selectedSkillIds: null,
  stateContract: 'unbound' as const, sessionGeneration: null, sessionStateVersion: null,
  stateSource: null, skillDraftRevision: 0, skillDraftDirty: false,
};
const emptyStreamState = {
  loading: false, progressSteps: [], abortController: null, activeRequestId: null,
  terminalStatus: null,
};

// null rejects the response. {} keeps the newer state but can still acknowledge
// an accepted message from this owned request. Versions only compare within an instance.
function mergeChatState(current: AgentChatState, incoming: DecodedChatState,
  source: 'detail' | 'accepted', bindingEpoch: number, draftRevision: number): Partial<AgentChatState> | null {
  const sameInstance = incoming.contract === 'authoritative'
    && incoming.sessionGeneration !== null && incoming.sessionGeneration === current.sessionGeneration;
  if (!sameInstance) {
    if (bindingEpoch !== current.instanceBindingEpoch) return null;
    if (source === 'accepted' && incoming.contract === 'authoritative' && current.sessionGeneration !== null) return null;
  }
  if (sameInstance && current.sessionStateVersion !== null && incoming.sessionStateVersion !== null) {
    if (incoming.sessionStateVersion < current.sessionStateVersion) return source === 'detail' ? null : {};
    if (source === 'accepted' && incoming.sessionStateVersion === current.sessionStateVersion
      && current.stateSource === 'detail') return {};
  }
  const bindingChanged = current.sessionGeneration !== incoming.sessionGeneration
    || (current.stateContract === 'authoritative' && incoming.contract === 'legacy');
  return {
    stateContract: incoming.contract,
    activeStockContext: incoming.activeStockContext,
    activeReportContext: incoming.activeReportContext,
    sessionGeneration: incoming.sessionGeneration,
    sessionStateVersion: incoming.sessionStateVersion,
    savedSkillIds: incoming.selectedSkillIds,
    ...(draftRevision === current.skillDraftRevision && (source === 'accepted' || !current.skillDraftDirty)
      ? { selectedSkillIds: incoming.selectedSkillIds, skillDraftDirty: false } : {}),
    stateSource: source,
    instanceBindingEpoch: current.instanceBindingEpoch + (bindingChanged ? 1 : 0),
  };
}

// Polling interval for checking running task progress (ms)
const POLL_INTERVAL_MS = 2000;
// After a refresh, a just-accepted turn can be persisted before the executor
// writes its first running heartbeat. Keep showing it as running briefly so a
// fast reload does not make an in-flight analysis look stopped.
const RUNNING_RECOVERY_GRACE_MS = 30_000;
// Synthetic step rendered while the server has an unfinished turn but has not
// reported live progress yet.
const RECOVERING_PROGRESS_STEP: ProgressStep = { type: 'recovering' };

function hasUnfinishedTurn(messages: ChatSessionDetail['messages']): boolean {
  const last = messages[messages.length - 1];
  // The server persists the user turn before execution. An accepted turn whose
  // assistant result is not stored yet is either still running or was
  // interrupted; both should keep the UI in a running/recovering state.
  return last?.role === 'user';
}

export const useAgentChatStore = create<AgentChatState & AgentChatActions>((set, get) => {
  let pollingTimer: ReturnType<typeof setInterval> | null = null;
  let recoveryDeadline: number | null = null;
  
  const stopPolling = () => {
    if (pollingTimer !== null) {
      clearInterval(pollingTimer);
      pollingTimer = null;
    }
  };

  const startPolling = (sessionId: string, graceMs = 0) => {
    stopPolling(); // Clear any existing timer
    recoveryDeadline = graceMs > 0 ? Date.now() + graceMs : null;
    
    const poll = async () => {
      const current = get();
      // Stop if session changed or no longer loading
      if (current.sessionId !== sessionId || !current.loading) {
        stopPolling();
        return;
      }
      
      try {
        const detail = await agentApi.getChatSessionMessages(sessionId);
        const state = get();
        
        // Check if session is still the same
        if (state.sessionId !== sessionId) {
          stopPolling();
          return;
        }
        
        const isRunning = detail.is_running || false;
        const runningProgress = detail.running_progress_steps || [];
        const recovering = !isRunning
          && recoveryDeadline !== null
          && Date.now() < recoveryDeadline
          && hasUnfinishedTurn(detail.messages);
        if (isRunning) recoveryDeadline = null;
        
        // Update progress steps. Server progress wins; otherwise keep the
        // recovering placeholder so the user still sees an active task.
        set({
          progressSteps: isRunning
            ? runningProgress
            : recovering ? [RECOVERING_PROGRESS_STEP] : [],
          loading: isRunning || recovering,
        });
        
        // Check for new messages (assistant response completed)
        const existingMessageIds = new Set(state.messages.map(m => m.id));
        const newMessages = detail.messages
          .filter(m => !existingMessageIds.has(m.id))
          .map(m => ({
            id: m.id,
            role: m.role,
            content: m.content,
            thinkingSteps: m.thinking_steps,
          }));
        
        if (newMessages.length > 0) {
          set({
            messages: [...state.messages, ...newMessages],
          });
        }
        
        // Stop polling if task completed (or the recovery grace expired)
        if (!isRunning && !recovering) {
          stopPolling();
          void get().loadSessions();
        }
      } catch (error) {
        // Silently ignore polling errors, will retry next interval
        console.debug('Polling error:', error);
      }
    };
    
    pollingTimer = setInterval(poll, POLL_INTERVAL_MS);
  };
  
  const resetSession = (sessionId: string) => {
    stopPolling();
    recoveryDeadline = null;
    const previous = get();
    previous.abortController?.abort();
    set({ ...emptySessionState, ...emptyStreamState, sessionId, messages: [], chatError: null,
      sessionEpoch: previous.sessionEpoch + 1, instanceBindingEpoch: previous.instanceBindingEpoch + 1,
      detailReadSequence: previous.detailReadSequence + 1 });
    localStorage.setItem(STORAGE_KEY_SESSION, sessionId);
  };
  const readDetail = async () => {
    const owner = get();
    const sequence = owner.detailReadSequence + 1;
    set({ detailReadSequence: sequence });
    const ownsRead = () => get().sessionId === owner.sessionId
      && get().sessionEpoch === owner.sessionEpoch && get().detailReadSequence === sequence;
    try {
      const detail = await agentApi.getChatSessionMessages(owner.sessionId);
      if (!ownsRead() || detail.session_id !== owner.sessionId) return;
      const decoded = decodeChatState(detail, 'detail');
      const current = get();
      const patch = mergeChatState(current, decoded, 'detail', owner.instanceBindingEpoch, owner.skillDraftRevision);
      
      // Restore thinking steps from persisted messages
      const messagesWithThinking = detail.messages.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        thinkingSteps: m.thinking_steps,
      }));
      
      // Restore running state if session is still running
      const isRunning = detail.is_running || false;
      const runningProgress = detail.running_progress_steps || [];
      const recovering = !isRunning && hasUnfinishedTurn(detail.messages);

      const replacedInstance = current.sessionGeneration !== null
        && current.sessionGeneration !== decoded.sessionGeneration;
      if (replacedInstance) current.abortController?.abort();
      // A live SSE stream owns transient running state. A detail read that was
      // issued before the stream can still update authoritative stock/Skill
      // state, but it must not flip loading off while the stream is active.
      const streamOwnsTransientState = current.abortController !== null && !replacedInstance;
      
      // 即使 mergeChatState 返回 null（例如页面刷新时状态不匹配），
      // 也要恢复消息和运行状态，否则用户会看到空白界面
      if (!patch) {
        // 状态合并失败，但仍然恢复消息和运行状态
        set({
          messages: messagesWithThinking,
          chatError: null,
          ...(streamOwnsTransientState ? {} : {
            loading: isRunning || recovering,
            progressSteps: isRunning
              ? runningProgress
              : recovering ? [RECOVERING_PROGRESS_STEP] : [],
          }),
        });
        
        // 启动轮询以持续更新运行状态
        if (!streamOwnsTransientState && (isRunning || recovering) && get().abortController === null) {
          startPolling(
            owner.sessionId,
            recovering && !isRunning ? RUNNING_RECOVERY_GRACE_MS : 0,
          );
        }
        return;
      }
      
      set({ 
        ...patch, 
        ...(replacedInstance ? emptyStreamState : {}),
        messages: messagesWithThinking,
        chatError: null,
        ...(streamOwnsTransientState ? {} : {
          loading: isRunning || recovering,
          progressSteps: isRunning
            ? runningProgress
            : recovering ? [RECOVERING_PROGRESS_STEP] : [],
        }),
      });
      
      // Start polling when the server still owns a running/recovering turn.
      // Do not poll while a live SSE stream owns this session: both would
      // append the same assistant message.
      if (!streamOwnsTransientState && (isRunning || recovering) && get().abortController === null) {
        startPolling(
          owner.sessionId,
          recovering && !isRunning ? RUNNING_RECOVERY_GRACE_MS : 0,
        );
      }
    } catch (error) {
      if (ownsRead()) set({ chatError: getParsedApiError(error) });
    }
  };

  return {
  messages: [],
  ...emptySessionState,
  sessionEpoch: 0,
  instanceBindingEpoch: 0,
  detailReadSequence: 0,
  loading: false,
  progressSteps: [],
  sessionId: getInitialSessionId(),
  sessions: [],
  sessionsLoading: false,
  chatError: null,
  currentRoute: '',
  completionBadge: false,
  hasInitialLoad: false,
  abortController: null,
  activeRequestId: null,
  terminalStatus: null,

  setSelectedSkillIds: (skillIds) => set((s) => ({ selectedSkillIds: [...skillIds],
    skillDraftRevision: s.skillDraftRevision + 1, skillDraftDirty: true })),

  setCurrentRoute: (path) => set({ currentRoute: path }),

  clearCompletionBadge: () => set({ completionBadge: false }),

  loadSessions: async () => {
    set({ sessionsLoading: true });
    try {
      const sessions = await agentApi.getChatSessions();
      set({ sessions });
    } catch {
      // Ignore load errors
    } finally {
      set({ sessionsLoading: false });
    }
  },

  loadInitialSession: async () => {
    const { hasInitialLoad, sessionEpoch, instanceBindingEpoch, activeRequestId, sessionId } = get();
    if (hasInitialLoad) return;
    const savedId = localStorage.getItem(STORAGE_KEY_SESSION);
    set({ hasInitialLoad: true, sessionsLoading: true });

    try {
      const sessionList = await agentApi.getChatSessions();
      if (get().sessionEpoch !== sessionEpoch) return;
      set({ sessions: sessionList });

      // A list can update presentation without owning a destructive restore.
      // First acceptance changes the instance binding, not the route epoch;
      // a newly started creation request must also keep its live stream.
      const current = get();
      if (current.instanceBindingEpoch !== instanceBindingEpoch
        || current.sessionId !== sessionId || current.activeRequestId !== activeRequestId) return;
      if (savedId) {
        // 关键修复：无论 savedId 是否在 sessionList 中，都应该尝试加载它
        // 因为 sessionList 有 limit 限制，可能不包含所有会话
        // 而且用户可能正在运行中的会话需要恢复
        if (get().sessionId !== savedId) resetSession(savedId);
        await readDetail();
      } else {
        localStorage.setItem(STORAGE_KEY_SESSION, get().sessionId);
      }
    } catch (error) {
      if (get().sessionEpoch === sessionEpoch) set({ chatError: getParsedApiError(error) });
    } finally {
      set({ sessionsLoading: false });
    }
  },

  switchSession: async (targetSessionId) => {
    const { sessionId, messages } = get();
    if (targetSessionId === sessionId && messages.length > 0) return;
    resetSession(targetSessionId);
    await readDetail();
  },

  refreshSession: readDetail,

  startNewChat: () => {
    resetSession(generateUUID());
  },

  stopStream: async () => {
    stopPolling();
    recoveryDeadline = null;
    const controller = get().abortController;
    if (controller) {
      set({ terminalStatus: 'stopped' });
      controller.abort();
    } else {
      // If we're in polling mode (no SSE controller), update loading state directly
      // The backend task will continue but we stop showing it as running
      set({ loading: false, progressSteps: [], terminalStatus: 'stopped' });
    }
  },

  startStream: async (payload, meta) => {
    if (get().loading) return;
    stopPolling();
    recoveryDeadline = null;
    const { abortController: prevAc, sessionId: storeSessionId, sessionEpoch,
      sessionGeneration, instanceBindingEpoch, skillDraftRevision } = get();
    prevAc?.abort();

    const ac = new AbortController();
    const requestId = payload.request_id || generateUUID();
    set({
      abortController: ac,
      activeRequestId: requestId,
      terminalStatus: null,
    });

    const streamSessionId = payload.session_id || storeSessionId;
    const ownsStream = () => {
      const state = get();
      return state.abortController === ac
        && state.activeRequestId === requestId
        && state.sessionId === streamSessionId && state.sessionEpoch === sessionEpoch;
    };
    const skillNames = meta?.skillNames?.length
      ? meta.skillNames
      : [meta?.skillName ?? '通用'];
    const skillName = skillNames.join('、');

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: payload.message,
      skills: payload.skills,
      skill: payload.skills?.[0],
      skillNames,
      skillName,
    };

    set({
      loading: true,
      progressSteps: [],
      chatError: null,
    });

    try {
      const response = await agentApi.chatStream(
        { ...payload, session_id: streamSessionId, request_id: requestId,
          ...(sessionGeneration ? { session_generation: sessionGeneration } : {}) },
        { signal: ac.signal },
      );
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let finalContent: string | null = null;
      let finalBackend: string | undefined;
      let finalClarification: StockClarification | null = null;
      let receivedDoneEvent = false;
      let acceptedEvent: StreamAcceptedEvent | null = null;
      const currentProgressSteps: ProgressStep[] = [];
      const protocolError = (message: string) => createParsedApiError({
        title: '请求未被接受',
        message: 'Agent 没有确认接收本次问题，请保留当前内容后重试。',
        rawMessage: message,
        category: 'upstream_network',
      });
      const processLine = (line: string) => {
        if (!line.startsWith('data: ') || !ownsStream() || ac.signal.aborted) return;

        const event = JSON.parse(line.slice(6)) as ProgressStep;
        if (event.type !== 'accepted' && (
          (event.request_id !== undefined && event.request_id !== requestId)
          || (event.session_id !== undefined && event.session_id !== streamSessionId)
        )) return;
        if (event.type === 'accepted') {
          if (acceptedEvent) {
            throw protocolError('Agent stream emitted accepted more than once.');
          }
          if (
            event.backend !== 'litellm'
            || event.request_id !== requestId
            || event.session_id !== streamSessionId
          ) {
            throw protocolError('Agent stream emitted an invalid accepted event.');
          }
          const decoded = decodeChatState(event, 'accepted');
          const statePatch = mergeChatState(get(), decoded, 'accepted', instanceBindingEpoch, skillDraftRevision);
          if (!statePatch) {
            // This request belonged to an instance superseded by a valid detail.
            ac.abort();
            return;
          }
          acceptedEvent = event as StreamAcceptedEvent;
          finalBackend = acceptedEvent.backend;
          set((s) => ({
            ...statePatch,
            messages: [...s.messages, { ...userMessage, backend: acceptedEvent!.backend }],
            sessions: s.sessions.some((x) => x.session_id === streamSessionId)
              ? s.sessions
              : [
                  {
                    session_id: streamSessionId,
                    title: payload.message.slice(0, 60),
                    message_count: 1,
                    created_at: new Date().toISOString(),
                    last_active: new Date().toISOString(),
                  },
                  ...s.sessions,
                ],
          }));
          meta?.onAccepted?.(acceptedEvent);
          return;
        }
        if (event.type === 'error') {
          const failureEvent = event as unknown as StreamFailureEvent;
          throw getStreamFailureError(
            failureEvent,
            '分析出错',
          );
        }
        if (!acceptedEvent) {
          throw protocolError(`Agent stream emitted ${event.type || 'an unknown event'} before accepted.`);
        }
        if (event.type === 'done') {
          receivedDoneEvent = true;
          const doneEvent = event as unknown as StreamFailureEvent;
          if (doneEvent.error_code === 'timeout') {
            set({ terminalStatus: 'timeout' });
            return;
          }
          if (doneEvent.success === false) {
            throw getStreamFailureError(
              doneEvent,
              '大模型调用出错，请检查 API Key 配置',
            );
          }
          finalContent = doneEvent.content ?? '';
          finalClarification = doneEvent.clarification ?? null;
          return;
        }

        currentProgressSteps.push(event);
        set((s) => ({ progressSteps: [...s.progressSteps, event] }));
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';

        for (const line of lines) {
          try {
            processLine(line);
          } catch (parseErr: unknown) {
            if (isParsedApiError(parseErr) || isApiRequestError(parseErr)) {
              throw parseErr;
            }
          }
        }
      }

      if (buf.trim().startsWith('data: ')) {
        try {
          processLine(buf.trim());
        } catch (parseErr: unknown) {
          if (isParsedApiError(parseErr) || isApiRequestError(parseErr)) {
            throw parseErr;
          }
        }
      }

      if (!acceptedEvent && !ac.signal.aborted) {
        throw protocolError('Agent stream ended before accepted.');
      }

      if (!receivedDoneEvent && !ac.signal.aborted) {
        throw createParsedApiError({
          title: '回复未完整返回',
          message: 'Agent 流式响应在完成前中断，请重试。',
          rawMessage: 'Agent stream ended before a done event was received.',
          category: 'upstream_network',
        });
      }

      const { currentRoute } = get();
      const shouldAppend = ownsStream() && !ac.signal.aborted && finalContent !== null;

      if (shouldAppend) {
        set((s) => ({
          messages: [
            ...s.messages,
            {
              id: (Date.now() + 1).toString(),
              role: 'assistant',
              content: finalContent || '（无内容）',
              skills: payload.skills,
              skill: payload.skills?.[0],
              skillNames,
              skillName,
              thinkingSteps: [...currentProgressSteps],
              backend: finalBackend,
              ...(finalClarification ? { stockClarification: finalClarification } : {}),
            },
          ],
        }));
      }

      if (ownsStream() && !ac.signal.aborted && currentRoute !== '/chat') {
        set({ completionBadge: true });
      }
    } catch (error: unknown) {
      if (isAbortError(error) || !ownsStream() || ac.signal.aborted) {
        // Aborted or superseded requests must not affect the active chat.
      } else {
        set({ chatError: getParsedApiError(error) });
        const { currentRoute } = get();
        if (currentRoute !== '/chat') {
          set({ completionBadge: true });
        }
      }
    } finally {
      if (ownsStream()) {
        recoveryDeadline = null;
        set({
          loading: false,
          progressSteps: [],
          abortController: null,
          activeRequestId: null,
        });
        await get().loadSessions();
      }
    }
  },
  };
});
