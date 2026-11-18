import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useShallow } from 'zustand/react/shallow';
import {
  Check,
  ChevronDown,
  Download,
  MoreHorizontal,
  PanelLeftOpen,
  Pencil,
  Send,
  Trash2,
  X,
} from 'lucide-react';
import { agentApi } from '../api/agent';
import { systemConfigApi } from '../api/systemConfig';
import { ApiErrorAlert, Badge, Button, ConfirmDialog, InlineAlert, ScrollArea } from '../components/common';
import { createParsedApiError, getParsedApiError } from '../api/error';
import type { AgentStatusResponse, SkillInfo } from '../api/agent';
import {
  useAgentChatStore,
  type Message,
  type StockClarificationIdentity,
} from '../stores/agentChatStore';
import ChatSmartSummaryCard from '../components/chat/ChatSmartSummaryCard';
import ChatMessageItem from '../components/chat/ChatMessageItem';
import ChatLiveProgress from '../components/chat/ChatLiveProgress';
import ChatInputArea from '../components/chat/ChatInputArea';
import ChatSessionSidebar from '../components/chat/ChatSessionSidebar';
import ChatGuideCards from '../components/chat/ChatGuideCards';
import ReportFollowUpCard from '../components/chat/ReportFollowUpCard';
import { downloadSessionInFormat, formatSessionAsMarkdown, type ExportFormat } from '../utils/chatExport';
import { buildScannerFollowUpPrompt } from '../utils/scannerFollowUp';
import type { ChatFollowUpContext } from '../utils/chatFollowUp';
import {
  ChatFollowUpLoadError,
  buildFollowUpPrompt,
  buildReportFollowUpContext,
  parseFollowUpRecordId,
  resolveChatReportDraft,
  sanitizeFollowUpStockCode,
  sanitizeFollowUpStockName,
} from '../utils/chatFollowUp';
import { isNearBottom } from '../utils/chatScroll';
import { getReportText } from '../utils/reportLanguage';
import {
  findMatchingStockCode,
  includesStockCode,
  resolveRegisteredIndexCanonical,
} from '../utils/stockCode';
import { useStockIndex } from '../hooks/useStockIndex';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { extractIntentHints } from '../utils/chatIntentHints';

// Quick question examples shown on empty state
type ActiveStockContext = Pick<ChatFollowUpContext, 'stock_code' | 'stock_name'>;

// Server-side report reference failures before the turn is accepted. They are
// surfaced on the report card instead of a duplicate generic chat error alert.
const REPORT_ERROR_CODES = new Set(['report_context_unavailable', 'report_context_invalid']);

type ReportDraftStatus = 'loading' | 'ready' | 'failed';

interface ReportFollowUpDraft {
  recordId: number;
  requestStockCode: string;
  requestStockName: string | null;
  stockCode: string;
  stockName: string | null;
  createdAt: string | null;
  status: ReportDraftStatus;
  error: string | null;
}

const QUICK_QUESTIONS: Array<{
  label: string;
  skill: string;
  stockContext?: ActiveStockContext;
}> = [
  { label: '用缠论分析茅台', skill: 'chan_theory', stockContext: { stock_code: '600519', stock_name: '贵州茅台' } },
  { label: '波浪理论看宁德时代', skill: 'wave_theory', stockContext: { stock_code: '300750', stock_name: '宁德时代' } },
  { label: '分析比亚迪趋势', skill: 'bull_trend', stockContext: { stock_code: '002594', stock_name: '比亚迪' } },
  { label: '用箱体震荡分析 A 股中芯国际 688981', skill: 'box_oscillation', stockContext: { stock_code: '688981', stock_name: '中芯国际' } },
  { label: '分析腾讯 hk00700', skill: 'bull_trend', stockContext: { stock_code: 'HK00700', stock_name: '腾讯控股' } },
  { label: '用情绪周期分析东方财富', skill: 'emotion_cycle', stockContext: { stock_code: '300059', stock_name: '东方财富' } },
];

const MAX_SELECTED_SKILLS = 3;
const CONTEXT_COMPRESSION_CONFIG_KEY = 'AGENT_CONTEXT_COMPRESSION_ENABLED';

type SessionListItem = {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string | null;
  last_active: string | null;
};

type SessionBucket = {
  key: 'today' | 'yesterday' | 'earlier';
  label: string;
  items: SessionListItem[];
};

// The same rename/delete menu is reachable from a history row and from the
// active conversation title; the origin decides which inline editor opens.
type SessionMenuOrigin = 'sidebar' | 'header';

const groupSessionsByDate = (
  sessions: SessionListItem[],
  labels: { today: string; yesterday: string; earlier: string },
): SessionBucket[] => {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const todayTimestamp = startOfToday.getTime();
  const yesterdayTimestamp = todayTimestamp - 24 * 60 * 60 * 1000;

  const buckets: SessionBucket[] = [
    { key: 'today', label: labels.today, items: [] },
    { key: 'yesterday', label: labels.yesterday, items: [] },
    { key: 'earlier', label: labels.earlier, items: [] },
  ];

  sessions.forEach((session) => {
    const rawTimestamp = session.last_active || session.created_at;
    const timestamp = rawTimestamp ? new Date(rawTimestamp).getTime() : 0;
    const bucket = timestamp >= todayTimestamp
      ? buckets[0]
      : timestamp >= yesterdayTimestamp
        ? buckets[1]
        : buckets[2];
    bucket.items.push(session);
  });

  return buckets.filter((bucket) => bucket.items.length > 0);
};

const getMessageSkillNames = (msg: Message): string[] => {
  if (msg.skillNames?.length) return msg.skillNames;
  if (msg.skillName) return [msg.skillName];
  if (msg.skills?.length) return msg.skills;
  if (msg.skill) return [msg.skill];
  return [];
};

const getMessageSkillLabel = (msg: Message): string => getMessageSkillNames(msg).join('、');

const ChatPage: React.FC = () => {
  const { t, language } = useUiLanguage();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [input, setInput] = useState('');
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [defaultSkillIds, setDefaultSkillIds] = useState<string[]>([]);
  const [showSkillDesc, setShowSkillDesc] = useState<string | null>(null);
  const [advancedOptionsOpen, setAdvancedOptionsOpen] = useState(false);
  const [expandedThinking, setExpandedThinking] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem('dsa.chat.sidebarCollapsed') === 'true');
  const [sessionMenu, setSessionMenu] = useState<{
    sessionId: string;
    top: number;
    left: number;
    origin: SessionMenuOrigin;
  } | null>(null);
  const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
  const [titleEditorOrigin, setTitleEditorOrigin] = useState<SessionMenuOrigin | null>(null);
  const [sessionTitle, setSessionTitle] = useState('');
  const [titleError, setTitleError] = useState('');
  const [titleSaving, setTitleSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [reportDraft, setReportDraft] = useState<ReportFollowUpDraft | null>(null);
  const [pendingReportRemoval, setPendingReportRemoval] = useState(false);
  const [followUpEvidenceError, setFollowUpEvidenceError] = useState<string | null>(null);
  const [sendToast, setSendToast] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);
  const [contextCompressionEnabled, setContextCompressionEnabled] = useState(false);
  const [contextCompressionLoaded, setContextCompressionLoaded] = useState(false);
  const [contextCompressionSaving, setContextCompressionSaving] = useState(false);
  const [contextCompressionConfigVersion, setContextCompressionConfigVersion] = useState('');
  const [contextCompressionMaskToken, setContextCompressionMaskToken] = useState('******');
  const [contextCompressionError, setContextCompressionError] = useState<string | null>(null);
  const [copiedMessages, setCopiedMessages] = useState<Set<string>>(new Set());
  const [resolvedClarifications, setResolvedClarifications] = useState<Set<string>>(() => new Set());
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const exportMenuRef = useRef<HTMLDivElement>(null);
  const exportButtonRef = useRef<HTMLButtonElement>(null);
  const [watchlistCodes, setWatchlistCodes] = useState<string[]>([]);
  const [isWatchlistActioning, setIsWatchlistActioning] = useState(false);
  const [watchlistMessage, setWatchlistMessage] = useState<string | null>(null);
  const [agentStatus, setAgentStatus] = useState<AgentStatusResponse | null>(null);
  const [agentStatusError, setAgentStatusError] = useState<string | null>(null);
  const [agentStatusChecking, setAgentStatusChecking] = useState(true);
  const [legacyIntakeNotice, setLegacyIntakeNotice] = useState(false);
  // The registry is used only to validate report hints, never to confirm Chat state.
  const { index: stockIndex, loading: stockIndexLoading } = useStockIndex();

  const watchlistMessageTimerRef = useRef<number | null>(null);
  const copyResetTimerRef = useRef<Partial<Record<string, number>>>({});
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const isMountedRef = useRef(true);
  const sendToastTimerRef = useRef<number | null>(null);
  const followUpHydrationTokenRef = useRef(0);
  const followUpHandledKeyRef = useRef<string | null>(null);
  const followUpContextRef = useRef<ChatFollowUpContext | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const pendingScrollBehaviorRef = useRef<ScrollBehavior>('auto');
  const agentStatusRequestIdRef = useRef(0);

  // Get localized text (default to Chinese)
  const text = getReportText('zh');

  // Cleanup timers on unmount
  useEffect(() => {
    const timers = copyResetTimerRef.current;
    return () => {
      if (sendToastTimerRef.current !== null) {
        window.clearTimeout(sendToastTimerRef.current);
      }
      Object.values(timers).forEach((timerId) => {
        if (timerId !== undefined) {
          window.clearTimeout(timerId);
        }
      });
    };
  }, []);

  // Set page title
  useEffect(() => {
    document.title = '问股 - DSA';
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const loadWatchlist = useCallback(async () => {
    try {
      const codes = await systemConfigApi.getWatchlist();
      if (isMountedRef.current) {
        setWatchlistCodes(codes);
      }
    } catch {
      // ignore error silently
    }
  }, []);

  useEffect(() => {
    void loadWatchlist();
  }, [loadWatchlist]);

  const stockInWatchlist = useCallback(
    (stockCode: string) => includesStockCode(watchlistCodes, stockCode),
    [watchlistCodes],
  );

  const handleToggleWatchlist = useCallback(
    async (stockCode: string) => {
      if (!stockCode || isWatchlistActioning) return;
      setIsWatchlistActioning(true);
      setWatchlistMessage(null);
      try {
        const existingStockCode = findMatchingStockCode(watchlistCodes, stockCode);
        if (existingStockCode) {
          const codes = await systemConfigApi.removeFromWatchlist(existingStockCode);
          if (isMountedRef.current) {
            setWatchlistCodes(codes);
            setWatchlistMessage(`已从自选中移除 ${stockCode}`);
          }
        } else {
          const codes = await systemConfigApi.addToWatchlist(stockCode);
          if (isMountedRef.current) {
            setWatchlistCodes(codes);
            setWatchlistMessage(`已加入自选 ${stockCode}`);
          }
        }
      } catch {
        if (isMountedRef.current) {
          setWatchlistMessage('操作失败，请重试');
        }
      } finally {
        if (isMountedRef.current) {
          setIsWatchlistActioning(false);
          if (watchlistMessageTimerRef.current !== null) {
            window.clearTimeout(watchlistMessageTimerRef.current);
          }
          watchlistMessageTimerRef.current = window.setTimeout(() => {
            if (isMountedRef.current) {
              setWatchlistMessage(null);
            }
          }, 3000);
        }
      }
    },
    [isWatchlistActioning, watchlistCodes],
  );

  // Shallow selector subscription (T40): the page re-renders only when one of
  // the fields it actually reads changes. Typing in the composer is local
  // state, so it never touches the store or the memoized message thread.
  const {
    messages,
    selectedSkillIds: sessionSelectedSkillIds,
    activeStockContext,
    activeReportContext,
    stateContract,
    loading,
    progressSteps,
    streamingContent,
    sessionId,
    sessions,
    sessionsLoading,
    hasInitialLoad,
    chatError,
    terminalStatus,
    intakeDraft,
    intakeBusy,
    intakeError,
    createIntake,
    updateIntakeAnswers,
    startIntakeAnalysis,
    skipIntake,
    clearIntake,
    cancelIntake,
    setSelectedSkillIds,
    loadSessions,
    loadInitialSession,
    switchSession,
    refreshSession,
    startStream,
    stopStream,
    clearCompletionBadge,
  } = useAgentChatStore(useShallow((state) => ({
    messages: state.messages,
    selectedSkillIds: state.selectedSkillIds,
    activeStockContext: state.activeStockContext,
    activeReportContext: state.activeReportContext,
    stateContract: state.stateContract,
    loading: state.loading,
    progressSteps: state.progressSteps,
    streamingContent: state.streamingContent,
    sessionId: state.sessionId,
    sessions: state.sessions,
    sessionsLoading: state.sessionsLoading,
    hasInitialLoad: state.hasInitialLoad,
    chatError: state.chatError,
    terminalStatus: state.terminalStatus,
    intakeDraft: state.intakeDraft,
    intakeBusy: state.intakeBusy,
    intakeError: state.intakeError,
    createIntake: state.createIntake,
    updateIntakeAnswers: state.updateIntakeAnswers,
    startIntakeAnalysis: state.startIntakeAnalysis,
    skipIntake: state.skipIntake,
    clearIntake: state.clearIntake,
    cancelIntake: state.cancelIntake,
    setSelectedSkillIds: state.setSelectedSkillIds,
    loadSessions: state.loadSessions,
    loadInitialSession: state.loadInitialSession,
    switchSession: state.switchSession,
    refreshSession: state.refreshSession,
    startStream: state.startStream,
    stopStream: state.stopStream,
    clearCompletionBadge: state.clearCompletionBadge,
  })));
  const selectedSkillIds = sessionSelectedSkillIds ?? defaultSkillIds;

  const closeTitleEditor = useCallback(() => {
    setEditingSessionId(null);
    setTitleEditorOrigin(null);
    setTitleError('');
  }, []);

  const saveSessionTitle = async () => {
    const title = sessionTitle.trim();
    if (!editingSessionId || titleSaving) return;
    if (!title) { setTitleError(t('chat.titleRequired')); return; }
    setTitleSaving(true);
    setTitleError('');
    try {
      await agentApi.renameChatSession(editingSessionId, title);
      closeTitleEditor();
      await loadSessions();
    } catch {
      setTitleError(t('chat.titleSaveFailed'));
    } finally {
      setTitleSaving(false);
    }
  };

  const activeStockCode = activeStockContext?.stock_code ?? null;

  const activeSession = sessions.find((session) => session.session_id === sessionId);
  const activeSessionTitle = activeSession?.title?.trim() || t('chat.untitled');

  const sessionGroups = useMemo(
    () => groupSessionsByDate(sessions, {
      today: t('chat.groupToday'),
      yesterday: t('chat.groupYesterday'),
      earlier: t('chat.groupEarlier'),
    }),
    [sessions, t],
  );

  useEffect(() => {
    if (!sessionMenu) return;
    const close = () => setSessionMenu(null);
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [sessionMenu]);

  // Close export menu when clicking outside
  useEffect(() => {
    if (!exportMenuOpen) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (exportMenuRef.current && !exportMenuRef.current.contains(event.target as Node)) {
        setExportMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [exportMenuOpen]);

  const handleExportSession = useCallback(async (format: ExportFormat) => {
    if (exporting) return;
    setExporting(true);
    setExportMenuOpen(false);
    try {
      await downloadSessionInFormat(messages, format);
    } catch (error) {
      console.error('Export failed:', error);
    } finally {
      setExporting(false);
    }
  }, [messages, exporting]);

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((value) => {
      const next = !value;
      localStorage.setItem('dsa.chat.sidebarCollapsed', String(next));
      return next;
    });
  }, []);

  const openSessionMenu = useCallback((
    event: React.MouseEvent<HTMLButtonElement>,
    targetSessionId: string,
    origin: SessionMenuOrigin = 'sidebar',
  ) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setSessionMenu((current) => (current?.sessionId === targetSessionId && current.origin === origin
      ? null
      : {
          sessionId: targetSessionId,
          top: rect.bottom + 4,
          left: Math.max(8, rect.right - 150),
          origin,
        }));
  }, []);

  const startRenamingSession = useCallback((session: SessionListItem, origin: SessionMenuOrigin = 'sidebar') => {
    setSessionMenu(null);
    setSessionTitle(session.title);
    setTitleError('');
    setEditingSessionId(session.session_id);
    setTitleEditorOrigin(origin);
  }, []);

  const syncScrollState = useCallback(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    const nearBottom = isNearBottom({
      scrollTop: viewport.scrollTop,
      clientHeight: viewport.clientHeight,
      scrollHeight: viewport.scrollHeight,
    });
    shouldStickToBottomRef.current = nearBottom;
    setShowJumpToBottom((prev) => (nearBottom ? false : prev));
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  }, []);

  const requestScrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    shouldStickToBottomRef.current = true;
    pendingScrollBehaviorRef.current = behavior;
    setShowJumpToBottom(false);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    syncScrollState();
  }, [syncScrollState]);

  useEffect(() => {
    syncScrollState();
  }, [syncScrollState, sessionId]);

  useEffect(() => {
    const behavior = pendingScrollBehaviorRef.current;
    const shouldAutoScroll = shouldStickToBottomRef.current;
    if (!shouldAutoScroll) {
      if (messages.length > 0 || progressSteps.length > 0 || loading || Boolean(streamingContent)) {
        setShowJumpToBottom(true);
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollToBottom(behavior);
      pendingScrollBehaviorRef.current = loading ? 'auto' : 'smooth';
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, progressSteps, loading, sessionId, streamingContent, scrollToBottom]);

  useEffect(() => {
    if (!loading) {
      pendingScrollBehaviorRef.current = 'smooth';
    }
  }, [loading]);

  useEffect(() => {
    clearCompletionBadge();
  }, [clearCompletionBadge]);

  useEffect(() => {
    loadInitialSession();
  }, [loadInitialSession]);

  useEffect(() => {
    agentApi.getSkills()
      .then((res) => {
        setSkills(res.skills);
        const defaultId =
          res.default_skill_id ||
          res.skills[0]?.id ||
          '';
        setDefaultSkillIds(defaultId ? [defaultId] : []);
      })
      .catch((error) => {
        console.error('Failed to load chat skills:', error);
      });
  }, []);

  const loadAgentStatus = useCallback(async () => {
    const requestId = agentStatusRequestIdRef.current + 1;
    agentStatusRequestIdRef.current = requestId;
    setAgentStatusChecking(true);
    try {
      const status = await agentApi.getStatus();
      if (!isMountedRef.current || agentStatusRequestIdRef.current !== requestId) return;
      setAgentStatus(status);
      setAgentStatusError(null);
    } catch (error: unknown) {
      if (!isMountedRef.current || agentStatusRequestIdRef.current !== requestId) return;
      setAgentStatus(null);
      setAgentStatusError(getParsedApiError(error).message);
    } finally {
      if (isMountedRef.current && agentStatusRequestIdRef.current === requestId) {
        setAgentStatusChecking(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadAgentStatus();
  }, [loadAgentStatus]);

  useEffect(() => {
    let active = true;

    void systemConfigApi.getConfig(false)
      .then((config) => {
        if (!active) {
          return;
        }
        const enabledItem = config.items.find((item) => item.key === CONTEXT_COMPRESSION_CONFIG_KEY);
        setContextCompressionEnabled(String(enabledItem?.value ?? '').trim().toLowerCase() === 'true');
        setContextCompressionConfigVersion(config.configVersion);
        setContextCompressionMaskToken(config.maskToken || '******');
        setContextCompressionLoaded(true);
        setContextCompressionError(null);
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        const parsed = getParsedApiError(error);
        setContextCompressionLoaded(false);
        setContextCompressionError(parsed.message || '无法读取上下文压缩配置');
        console.error('Failed to load context compression setting:', error);
      });

    return () => {
      active = false;
    };
  }, []);

  const updateContextCompressionEnabled = useCallback(
    async (nextEnabled: boolean) => {
      if (!contextCompressionLoaded || contextCompressionSaving) {
        return;
      }

      const previousEnabled = contextCompressionEnabled;
      setContextCompressionEnabled(nextEnabled);
      setContextCompressionSaving(true);
      setContextCompressionError(null);

      try {
        const result = await systemConfigApi.update({
          configVersion: contextCompressionConfigVersion,
          maskToken: contextCompressionMaskToken,
          reloadNow: true,
          items: [
            {
              key: CONTEXT_COMPRESSION_CONFIG_KEY,
              value: nextEnabled ? 'true' : 'false',
            },
          ],
        });
        setContextCompressionConfigVersion(result.configVersion || contextCompressionConfigVersion);
      } catch (error) {
        const parsed = getParsedApiError(error);
        setContextCompressionEnabled(previousEnabled);
        setContextCompressionError(parsed.message || '上下文压缩设置保存失败');
      } finally {
        setContextCompressionSaving(false);
      }
    },
    [
      contextCompressionConfigVersion,
      contextCompressionEnabled,
      contextCompressionLoaded,
      contextCompressionMaskToken,
      contextCompressionSaving,
    ],
  );

  const availableSkillIds = new Set(skills.map((skill) => skill.id));
  const quickQuestions = QUICK_QUESTIONS.filter((question) => availableSkillIds.size === 0 || availableSkillIds.has(question.skill));
  // A pending report draft blocks every send entry until it is ready or removed.
  const reportDraftBlocked = reportDraft !== null && reportDraft.status !== 'ready';
  // Pre-accept report failures are already explained by the report card.
  const reportChatErrorSuppressed = chatError !== null && chatError.code !== undefined
    && REPORT_ERROR_CODES.has(chatError.code) && reportDraft !== null;
  const skillLimitReached = selectedSkillIds.length >= MAX_SELECTED_SKILLS;
  const agentConfirmedUnavailable = Boolean(agentStatus && !agentStatus.available);
  const agentAvailable = Boolean(agentStatus?.available) && !agentStatusChecking;
  const guidedIntakeSupported = Boolean(agentStatus?.guidedIntake);
  const agentUnavailableMessage = agentStatus?.errorCode === 'backend_removed'
    ? t('settings.agentBackendRemoved')
    : agentStatus?.errorCode === 'agent_mode_disabled'
      ? t('chat.agentModeDisabled')
      : t('chat.defaultUnavailableMessage');
  const agentUnavailableError = agentConfirmedUnavailable
    ? createParsedApiError({
        title: t('chat.agentBackendUnavailableTitle'),
        message: agentUnavailableMessage,
        rawMessage: `${agentStatus?.errorCode || 'capability_unsupported'}: ${agentStatus?.message || ''}`,
        category: 'upstream_network',
      })
    : null;

  const getSkillNames = useCallback(
    (skillIds: string[]) => skillIds.map((id) => skills.find((s) => s.id === id)?.name || id),
    [skills],
  );

  const normalizeSelectedSkillIds = useCallback((skillIds: string[]) => {
    const normalized: string[] = [];
    for (const skillId of skillIds) {
      const cleaned = skillId.trim();
      if (cleaned && !normalized.includes(cleaned)) {
        normalized.push(cleaned);
      }
    }
    return normalized.slice(0, MAX_SELECTED_SKILLS);
  }, []);

  const toggleSkillSelection = useCallback((skillId: string) => {
    if (selectedSkillIds.includes(skillId)) {
      setSelectedSkillIds(selectedSkillIds.filter((id) => id !== skillId));
      return;
    }
    if (selectedSkillIds.length < MAX_SELECTED_SKILLS) {
      setSelectedSkillIds([...selectedSkillIds, skillId]);
    }
  }, [selectedSkillIds, setSelectedSkillIds]);

  const clearFollowUpParams = useCallback(() => {
    if (searchParams.size > 0) {
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const resetReportDraft = useCallback(() => {
    followUpHydrationTokenRef.current += 1;
    followUpHandledKeyRef.current = null;
    followUpContextRef.current = null;
    setReportDraft(null);
    setPendingReportRemoval(false);
    clearFollowUpParams();
  }, [clearFollowUpParams]);

  const handleStartNewChat = useCallback(() => {
    resetReportDraft();
    setSessionMenu(null);
    closeTitleEditor();
    requestScrollToBottom('auto');
    useAgentChatStore.getState().startNewChat();
    setSidebarOpen(false);
  }, [closeTitleEditor, requestScrollToBottom, resetReportDraft]);

  const handleSwitchSession = useCallback((targetSessionId: string) => {
    setSessionMenu(null);
    closeTitleEditor();
    if (targetSessionId === sessionId) {
      setSidebarOpen(false);
      return;
    }
    resetReportDraft();
    requestScrollToBottom('auto');
    switchSession(targetSessionId);
    setSidebarOpen(false);
  }, [closeTitleEditor, requestScrollToBottom, resetReportDraft, sessionId, switchSession]);

  const confirmDelete = useCallback(() => {
    if (!deleteConfirmId) return;
    agentApi.deleteChatSession(deleteConfirmId)
      .then(() => {
        loadSessions();
        if (deleteConfirmId === sessionId) {
          handleStartNewChat();
        }
      })
      .catch((error) => {
        console.error('Failed to delete chat session:', error);
      });
    setDeleteConfirmId(null);
  }, [deleteConfirmId, sessionId, loadSessions, handleStartNewChat]);

  const loadReportDraft = useCallback((draft: ReportFollowUpDraft) => {
    const loadToken = ++followUpHydrationTokenRef.current;
    setReportDraft((current) => current?.recordId === draft.recordId
      ? { ...current, status: 'loading', error: null }
      : current);
    void resolveChatReportDraft({
      stockCode: draft.requestStockCode,
      recordId: draft.recordId,
      index: stockIndex,
    }).then((resolved) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== loadToken) return;
      setReportDraft((current) => current?.recordId === draft.recordId ? {
        recordId: draft.recordId,
        requestStockCode: draft.requestStockCode,
        requestStockName: draft.requestStockName,
        stockCode: resolved.stockCode,
        stockName: resolved.stockName,
        createdAt: resolved.createdAt,
        status: 'ready',
        error: null,
      } : current);
    }).catch((error: unknown) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== loadToken) return;
      const detailKey = error instanceof ChatFollowUpLoadError && error.code === 'report_mismatch'
        ? 'chat.reportMismatchDetail'
        : error instanceof ChatFollowUpLoadError && error.code === 'report_not_supported'
          ? 'chat.reportNotSupportedDetail'
          : 'chat.reportUnavailableDetail';
      setReportDraft((current) => current?.recordId === draft.recordId
        ? { ...current, status: 'failed', error: t(detailKey) }
        : current);
    });
  }, [stockIndex, t]);

  const handleRemoveReportDraft = useCallback(() => {
    const removed = reportDraft;
    followUpHydrationTokenRef.current += 1;
    setReportDraft(null);
    if (removed) {
      // Removing the report keeps the original question as a regular follow-up.
      followUpContextRef.current = {
        stock_code: removed.requestStockCode,
        stock_name: removed.requestStockName,
      };
    }
    clearFollowUpParams();
  }, [clearFollowUpParams, reportDraft]);

  const handleRetryReportDraft = useCallback(() => {
    if (!reportDraft) return;
    loadReportDraft(reportDraft);
  }, [loadReportDraft, reportDraft]);

  const handleRetryActiveReport = useCallback(() => {
    void refreshSession();
  }, [refreshSession]);

  const handleRemoveActiveReport = useCallback(() => {
    if (!activeReportContext || activeReportContext.status !== 'available') return;
    // The persisted pointer stays until the next accepted turn; the next send
    // carries `report_record_id: null` to clear it server-side.
    setPendingReportRemoval(true);
  }, [activeReportContext]);

  // Handle follow-up from report page: ?stock=600519&name=贵州茅台&recordId=xxx
  useEffect(() => {
    const rawStockCode = searchParams.get('stock');
    if (!rawStockCode) {
      // Nothing to follow up — only clear when there actually ARE stale query
      // params. Skipping the empty case avoids redundant `replace: true`
      // navigations on every effect re-run (the gate below legitimately re-runs
      // as status/registry state settles), which would otherwise clobber
      // subsequent RouterProvider navigation state asserted by other tests.
      if (searchParams.size === 0) {
        return;
      }
      setSearchParams({}, { replace: true });
      return;
    }

    if (stockIndexLoading) {
      return;
    }
    if (agentStatusChecking) {
      return;
    }

    // A report draft keeps its URL params so a refresh can re-load the exact
    // record; other follow-ups preserve the existing clear-immediately contract.
    const paramKey = searchParams.toString();
    if (followUpHandledKeyRef.current === paramKey) {
      return;
    }
    followUpHandledKeyRef.current = paramKey;

    // Registry canonical first for explicit index follow-ups (sh000016 /
    // 000016.SH / 930955.CSI / csi930955) so the report-follow-up canonical is
    // preserved end-to-end; the sanitize contract is untouched and remains the
    // stock fail-open when the registry is unavailable or the code unregistered.
    const registryCanonical = resolveRegisteredIndexCanonical(stockIndex, rawStockCode);
    const stock = registryCanonical ?? sanitizeFollowUpStockCode(rawStockCode);
    const name = sanitizeFollowUpStockName(searchParams.get('name'));
    const recordId = parseFollowUpRecordId(searchParams.get('recordId'));

    if (!stock) {
      setSearchParams({}, { replace: true });
      return;
    }

    const hydrationToken = ++followUpHydrationTokenRef.current;
    const initialPrompt = buildFollowUpPrompt(stock, name);
    const scannerSignal = searchParams.get('scannerSignal');
    setFollowUpEvidenceError(null);
    setInput(initialPrompt);
    setPendingReportRemoval(false);
    followUpContextRef.current = {
      stock_code: stock,
      stock_name: name,
    };

    if (recordId !== undefined) {
      loadReportDraft({
        recordId,
        requestStockCode: stock,
        requestStockName: name,
        stockCode: '',
        stockName: null,
        createdAt: null,
        status: 'loading',
        error: null,
      });
    } else {
      setReportDraft(null);
    }

    if (scannerSignal) {
      void buildScannerFollowUpPrompt(stock, scannerSignal, searchParams.get('scannerRevision'))
        .then((prompt) => {
          if (!isMountedRef.current || followUpHydrationTokenRef.current !== hydrationToken || !prompt) return;
          setInput((current) => current === initialPrompt ? prompt : current);
        })
        .catch((error: unknown) => {
          if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
            setFollowUpEvidenceError(error instanceof Error ? error.message : '未能载入冻结证据，请从信号详情重试');
          }
        });
    }

    if (recordId === undefined) {
      setSearchParams({}, { replace: true });
    }
  }, [clearFollowUpParams, loadReportDraft, searchParams, setSearchParams, stockIndex, stockIndexLoading, agentStatusChecking]);

  const handleSend = useCallback(
    async (
      overrideMessage?: string,
      overrideSkillIds?: string[],
      overrideStockContext?: ActiveStockContext,
    ) => {
      const msgText = (overrideMessage ?? input).trim();
      // A report draft must finish client validation before any send entry
      // (button, Enter, quick question or clarification confirmation) submits.
      const reportDraftBlocked = reportDraft !== null && reportDraft.status !== 'ready';
      // T5: prevent duplicate submissions during intake creation
      if (!msgText || loading || intakeBusy || !agentAvailable || !agentStatus || intakeDraft
        || reportDraftBlocked) return;
      if (overrideMessage !== undefined) {
        setInput(msgText);
      }
      const requestedSkillIds = overrideSkillIds ?? sessionSelectedSkillIds;
      const usedSkillIds = normalizeSelectedSkillIds(
        requestedSkillIds ?? selectedSkillIds,
      );
      const usedSkillNames = usedSkillIds.length > 0 ? getSkillNames(usedSkillIds) : ['通用'];
      const sentReportDraft = reportDraft;
      const sentRemoval = pendingReportRemoval;
      const removalBase = followUpContextRef.current ?? (activeStockContext
        ? { stock_code: activeStockContext.stock_code, stock_name: activeStockContext.stock_name }
        : null);
      // The server is the only authority for report content; this request
      // carries a record pointer and a stock hint, never a copied summary.
      const contextForSend: ChatFollowUpContext | undefined = overrideStockContext
        ?? (sentReportDraft?.status === 'ready'
          ? buildReportFollowUpContext(sentReportDraft.stockCode, sentReportDraft.stockName, sentReportDraft.recordId)
          : sentRemoval
            ? {
                stock_code: removalBase?.stock_code ?? '',
                stock_name: removalBase?.stock_name ?? null,
                report_record_id: null,
              }
            : followUpContextRef.current ?? undefined);

      const payload = {
        message: msgText,
        session_id: sessionId,
        ...(requestedSkillIds !== null
          ? { skills: normalizeSelectedSkillIds(requestedSkillIds) }
          : {}),
        context: contextForSend ?? undefined,
      };
      const streamMeta = {
        skillNames: usedSkillNames,
        skillName: usedSkillNames.join('、'),
        onAccepted: () => {
          followUpHydrationTokenRef.current += 1;
          followUpContextRef.current = null;
          if (sentReportDraft) {
            setReportDraft((current) => current?.recordId === sentReportDraft.recordId ? null : current);
            clearFollowUpParams();
          }
          if (sentRemoval) setPendingReportRemoval(false);
          setInput('');
          requestScrollToBottom('smooth');
        },
        onError: ({ error, accepted }: { error: { code?: string; message: string }; accepted: boolean }) => {
          // Before acceptance the turn was not persisted: keep the draft and
          // the question so the user can retry or remove the report.
          if (accepted || !sentReportDraft || !error.code || !REPORT_ERROR_CODES.has(error.code)) return;
          setReportDraft((current) => current?.recordId === sentReportDraft.recordId
            ? { ...current, status: 'failed', error: error.message || t('chat.reportUnavailableDetail') }
            : current);
        },
      };
      // First valid question of a new session: prepare 1-5 controlled choices.
      // No research, quotes or news lookups start while a draft is pending.
      const isFirstQuestion = messages.length === 0 && hasInitialLoad && !intakeDraft;
      if (isFirstQuestion && guidedIntakeSupported) {
        const hints = extractIntentHints(msgText, stockIndex);
        const success = await createIntake(msgText, payload, streamMeta, hints);
        // T5: only clear input on success to preserve user's question on failure
        if (success) {
          setInput('');
          requestScrollToBottom('smooth');
        }
        return;
      }
      if (isFirstQuestion) setLegacyIntakeNotice(true);
      await startStream(payload, streamMeta);
    },
    [activeStockContext, agentAvailable, agentStatus, clearFollowUpParams, createIntake, getSkillNames,
      guidedIntakeSupported, hasInitialLoad, input, intakeBusy, intakeDraft, loading, messages.length,
      normalizeSelectedSkillIds, pendingReportRemoval, reportDraft, requestScrollToBottom, selectedSkillIds,
      sessionId, sessionSelectedSkillIds, startStream, stockIndex, t],
  );

  const handleStockClarificationDecision = useCallback((
    message: Message,
    decision: 'switch' | 'keep',
    target: StockClarificationIdentity | null,
  ) => {
    setResolvedClarifications((previous) => {
      const next = new Set(previous);
      next.add(message.id);
      return next;
    });
    if (loading || !agentAvailable) {
      composerRef.current?.focus();
      return;
    }
    const switchTarget = target ?? message.stockClarification?.recommended ?? null;
    if (decision === 'switch' && switchTarget) {
      const label = switchTarget.stock_name
        ? `${switchTarget.stock_name}（${switchTarget.stock_code}）`
        : switchTarget.stock_code;
      void handleSend(`确认，分析 ${label}`, undefined, {
        stock_code: switchTarget.stock_code,
        stock_name: switchTarget.stock_name,
      });
      return;
    }
    if (activeStockContext) {
      void handleSend('确认，继续分析当前标的', undefined, {
        stock_code: activeStockContext.stock_code,
        stock_name: activeStockContext.stock_name,
      });
      return;
    }
    composerRef.current?.focus();
  }, [activeStockContext, agentAvailable, handleSend, loading]);

  // Stable indirection so memoized message items do not re-render on every
  // keystroke (T40). `handleSend` depends on `input`, so it must not be passed
  // down to the message tree directly.
  const handleSendRef = useRef(handleSend);
  useEffect(() => {
    handleSendRef.current = handleSend;
  }, [handleSend]);
  const handleFollowUpQuestion = useCallback((question: string) => {
    void handleSendRef.current(question);
  }, []);
  const focusComposer = useCallback(() => {
    composerRef.current?.focus();
  }, []);

  const clarificationDecisionRef = useRef(handleStockClarificationDecision);
  useEffect(() => {
    clarificationDecisionRef.current = handleStockClarificationDecision;
  }, [handleStockClarificationDecision]);
  const handleMessageClarificationDecision = useCallback((
    message: Message,
    decision: 'switch' | 'keep',
    target: StockClarificationIdentity | null,
  ) => {
    clarificationDecisionRef.current(message, decision, target);
  }, []);

  const lastMessage = messages[messages.length - 1];
  const activeClarificationMessageId = lastMessage?.role === 'assistant'
    && lastMessage.stockClarification
    && !resolvedClarifications.has(lastMessage.id)
    ? lastMessage.id
    : null;
  const clarificationAutoConfirmEnabled = Boolean(activeClarificationMessageId)
    && !loading
    && agentAvailable
    && !input.trim();

  const handleQuickQuestion = (q: (typeof QUICK_QUESTIONS)[0]) => {
    setSelectedSkillIds([q.skill]);
    handleSend(q.label, [q.skill], q.stockContext);
  };

  const showSendFeedback = useCallback((nextToast: { type: 'success' | 'error'; message: string }, durationMs: number) => {
    if (sendToastTimerRef.current !== null) {
      window.clearTimeout(sendToastTimerRef.current);
    }
    setSendToast(nextToast);
    sendToastTimerRef.current = window.setTimeout(() => {
      setSendToast(null);
      sendToastTimerRef.current = null;
    }, durationMs);
  }, []);

  const toggleCategory = useCallback((msgId: string, category: string) => {
    const key = `${msgId}:${category}`;
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    // Also expand the thinking block if not already expanded
    setExpandedThinking((prev) => {
      const next = new Set(prev);
      if (!next.has(msgId)) next.add(msgId);
      return next;
    });
  }, []);

  const copyMessageToClipboard = useCallback(async (msgId: string, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setCopiedMessages((prev) => new Set(prev).add(msgId));
      const existingTimer = copyResetTimerRef.current[msgId];
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }
      copyResetTimerRef.current[msgId] = window.setTimeout(() => {
        setCopiedMessages((prev) => {
          const next = new Set(prev);
          next.delete(msgId);
          return next;
        });
        delete copyResetTimerRef.current[msgId];
      }, 2000);
    } catch (err) {
      console.error('Copy failed:', err);
    }
  }, []);

  const downloadMessageAsMarkdown = useCallback((msg: Message) => {
    const skillLabel = getMessageSkillLabel(msg);
    const heading = msg.role === 'user' ? '# 用户消息' : `# AI 回复${skillLabel ? ` · ${skillLabel}` : ''}`;
    const content = [heading, '', msg.content].join('\n');
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${msg.role === 'user' ? 'user' : 'assistant'}-message-${msg.id}.md`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  }, []);



  const selectedSkillSummary = selectedSkillIds.length > 0
    ? getSkillNames(selectedSkillIds).join('、')
    : '通用分析';

  const sessionMenuTarget = sessionMenu
    ? sessions.find((session) => session.session_id === sessionMenu.sessionId) ?? null
    : null;

  const sidebarContent = (
    <ChatSessionSidebar
      t={t}
      sessionGroups={sessionGroups}
      sessionsCount={sessions.length}
      sessionsLoading={sessionsLoading}
      sessionId={sessionId}
      menuSessionId={sessionMenu?.sessionId ?? null}
      editingSessionId={editingSessionId}
      titleEditorOrigin={titleEditorOrigin}
      sessionTitle={sessionTitle}
      titleSaving={titleSaving}
      titleError={titleError}
      onStartNewChat={handleStartNewChat}
      onToggleSidebar={toggleSidebar}
      onSwitchSession={handleSwitchSession}
      onChangeTitle={setSessionTitle}
      onSaveTitle={() => { void saveSessionTitle(); }}
      onCloseTitleEditor={closeTitleEditor}
      onOpenMenu={openSessionMenu}
    />
  );


  const objectStatusText = activeStockContext
    ? t('chat.currentObject', {
        object: `${activeStockContext.stock_name ? `${activeStockContext.stock_name} · ` : ''}${activeStockContext.stock_code}`,
      })
    : t(stateContract === 'legacy' ? 'chat.objectUnconfirmedLegacy' : 'chat.objectUnconfirmed');

  return (
    <div data-testid="chat-workspace" className="research-chat chat-workspace">
      {/* Desktop conversation list */}
      {!sidebarCollapsed && (
        <aside className="chat-sessions-panel">
          {sidebarContent}
        </aside>
      )}

      {/* Mobile conversation list */}
      {sidebarOpen && (
        <div className="chat-mobile-drawer fixed inset-0 z-40" onClick={() => setSidebarOpen(false)}>
          <div className="page-drawer-overlay absolute inset-0" />
          <div
            className="chat-sessions-panel chat-sessions-drawer absolute bottom-0 left-0 top-0 flex w-[17.5rem] flex-col overflow-hidden shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            {sidebarContent}
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={Boolean(deleteConfirmId)}
        title="删除对话"
        message="删除后，该对话将不可恢复，确认删除吗？"
        confirmText="删除"
        cancelText="取消"
        isDanger
        onConfirm={confirmDelete}
        onCancel={() => setDeleteConfirmId(null)}
      />

      {sessionMenu && sessionMenuTarget ? (
        <>
          <button
            type="button"
            className="chat-menu-backdrop"
            aria-label={t('common.close')}
            onClick={() => setSessionMenu(null)}
          />
          <div
            className="chat-session-menu"
            role="menu"
            aria-label={t('chat.moreActions')}
            style={{ top: sessionMenu.top, left: sessionMenu.left }}
          >
            <button
              type="button"
              role="menuitem"
              className="chat-session-menu-item"
              onClick={() => startRenamingSession(sessionMenuTarget, sessionMenu?.origin ?? 'sidebar')}
            >
              <Pencil size={13} aria-hidden="true" />
              {t('chat.rename')}
            </button>
            <button
              type="button"
              role="menuitem"
              className="chat-session-menu-item is-danger"
              aria-label={`${t('chat.deleteSession')} ${sessionMenuTarget.title}`}
              onClick={() => {
                setSessionMenu(null);
                setDeleteConfirmId(sessionMenuTarget.session_id);
              }}
            >
              <Trash2 size={13} aria-hidden="true" />
              {t('chat.deleteSession')}
            </button>
          </div>
        </>
      ) : null}

      <section className="chat-main">
        <header className="chat-topbar">
          <div className="chat-topbar-main">
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="chat-icon-btn chat-history-toggle-mobile"
              aria-label="历史对话"
            >
              <PanelLeftOpen size={17} aria-hidden="true" />
            </button>
            {sidebarCollapsed ? (
              <button
                type="button"
                onClick={toggleSidebar}
                className="chat-icon-btn chat-history-toggle-desktop"
                aria-label={t('chat.showHistory')}
              >
                <PanelLeftOpen size={17} aria-hidden="true" />
              </button>
            ) : null}
            <h1 className="chat-topbar-title">问股</h1>
            <span className="chat-topbar-divider" aria-hidden="true">/</span>
            {editingSessionId === sessionId && titleEditorOrigin === 'header' ? (
              <form className="chat-title-editor-inline" onSubmit={(event) => { event.preventDefault(); void saveSessionTitle(); }}>
                <input
                  aria-label={t('chat.editTitle')}
                  value={sessionTitle}
                  maxLength={60}
                  autoFocus
                  onChange={(event) => setSessionTitle(event.target.value)}
                  onKeyDown={(event) => { if (event.key === 'Escape') closeTitleEditor(); }}
                />
                <button type="submit" disabled={titleSaving} aria-label={t('chat.saveTitle')}>
                  <Check size={14} aria-hidden="true" />
                </button>
                <button type="button" disabled={titleSaving} onClick={closeTitleEditor} aria-label={t('common.cancel')}>
                  <X size={14} aria-hidden="true" />
                </button>
                {titleError ? <span role="alert" className="chat-title-error">{titleError}</span> : null}
              </form>
            ) : (
              <div className="chat-current-title-wrap">
                <span className="chat-current-title">{activeSessionTitle}</span>
                {activeSession ? (
                  <button
                    type="button"
                    className="chat-current-more"
                    aria-label={`${t('chat.currentSessionActions')} ${activeSessionTitle}`}
                    aria-haspopup="menu"
                    aria-expanded={sessionMenu?.sessionId === sessionId && sessionMenu.origin === 'header'}
                    onClick={(event) => openSessionMenu(event, sessionId, 'header')}
                  >
                    <MoreHorizontal size={15} aria-hidden="true" />
                  </button>
                ) : null}
              </div>
            )}
          </div>
          <div className="chat-topbar-actions">
            {agentStatus ? (
              <Badge
                variant={agentStatus.errorCode === 'backend_removed' ? 'warning' : 'history'}
                size="sm"
              >
                {t(agentStatus.errorCode === 'backend_removed' ? 'settings.agentBackendRemovedLabel' : 'chat.defaultBackendBadge')}
              </Badge>
            ) : null}
            {messages.length > 0 && (
              <>
                <div className="chat-export-dropdown" ref={exportMenuRef}>
                  <button
                    type="button"
                    className="chat-icon-btn"
                    ref={exportButtonRef}
                    onClick={() => setExportMenuOpen(!exportMenuOpen)}
                    disabled={exporting}
                    aria-label={t('chat.exportSession')}
                    aria-haspopup="menu"
                    aria-expanded={exportMenuOpen}
                  >
                    {exporting ? (
                      <span className="chat-send-spinner" aria-hidden="true" />
                    ) : (
                      <Download size={16} aria-hidden="true" />
                    )}
                  </button>
                  {exportMenuOpen && (
                    <div className="chat-export-menu" role="menu">
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => handleExportSession('md')}
                      >
                        {t('chat.exportAsMarkdown')}
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => handleExportSession('pdf')}
                      >
                        {t('chat.exportAsPdf')}
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        onClick={() => handleExportSession('word')}
                      >
                        {t('chat.exportAsWord')}
                      </button>
                    </div>
                  )}
                </div>
                <button
                  type="button"
                  className="chat-icon-btn"
                  disabled={sending}
                  aria-busy={sending}
                  onClick={async () => {
                    if (sending) return;
                    setSending(true);
                    setSendToast(null);
                    try {
                      const content = formatSessionAsMarkdown(messages);
                      await agentApi.sendChat(content);
                      showSendFeedback({ type: 'success', message: '已发送到通知渠道' }, 3000);
                    } catch (err) {
                      const parsed = getParsedApiError(err);
                      showSendFeedback({
                        type: 'error',
                        message: parsed.message || '发送失败',
                      }, 5000);
                    } finally {
                      setSending(false);
                    }
                  }}
                  aria-label="发送到已配置的通知机器人/邮箱"
                >
                  {sending
                    ? <span className="chat-send-spinner" aria-hidden="true" />
                    : <Send size={16} aria-hidden="true" />}
                </button>
              </>
            )}
          </div>
        </header>

        <div className="chat-body">
          <ScrollArea
            className="chat-thread-scroll"
            viewportRef={messagesViewportRef}
            onScroll={handleMessagesScroll}
            viewportClassName="chat-thread-viewport"
            testId="chat-message-scroll"
          >
            <div className="chat-thread">
              {messages.length === 0 && !loading && hasInitialLoad && !intakeDraft ? (
                <ChatGuideCards
                  t={t}
                  questions={quickQuestions}
                  disabled={!agentAvailable || intakeBusy || reportDraftBlocked}
                  onSelect={(index) => {
                    const question = quickQuestions[index];
                    if (question) handleQuickQuestion(question);
                  }}
                />
              ) : (
                <>
                  {intakeDraft && (
                    <div className="chat-msg chat-msg-user" data-testid="intake-original-message">
                      <div className="chat-user-bubble">{intakeDraft.original_message}</div>
                    </div>
                  )}
                  {intakeDraft && (
                    <ChatSmartSummaryCard
                      questions={intakeDraft.questions}
                      answers={intakeDraft.answers}
                      status={intakeDraft.status}
                      originalMessage={intakeDraft.original_message}
                      isLoading={intakeBusy}
                      onConfirm={async (a) => {
                        const saved = await updateIntakeAnswers(a, 'confirm');
                        if (saved) await startIntakeAnalysis();
                        return saved;
                      }}
                      onSkip={async () => {
                        await skipIntake();
                        return !useAgentChatStore.getState().intakeError;
                      }}
                      onCancel={() => {
                        const draft = useAgentChatStore.getState().intakeDraft;
                        if (draft) setInput(draft.original_message);
                        void cancelIntake();
                        composerRef.current?.focus();
                      }}
                      onUpdateAnswer={(questionId, optionId) => {
                        const currentAnswers = intakeDraft.answers || [];
                        const existingIndex = currentAnswers.findIndex(a => a.question_id === questionId);
                        let newAnswers;
                        if (existingIndex >= 0) {
                          newAnswers = currentAnswers.map((a, i) => 
                            i === existingIndex 
                              ? { ...a, option_ids: [optionId] }
                              : a
                          );
                        } else {
                          newAnswers = [...currentAnswers, { question_id: questionId, option_ids: [optionId] }];
                        }
                        updateIntakeAnswers(newAnswers, 'save');
                      }}
                    />
                  )}
                  {messages.map((msg) => {
                  const skillLabel = getMessageSkillLabel(msg);
                  return (
                    <ChatMessageItem
                      key={msg.id}
                      message={msg}
                      skillLabel={skillLabel}
                      backendBadgeLabel={msg.backend
                        ? t(msg.backend === 'codex_app_server'
                          ? 'chat.historicalCodexBackendBadge'
                          : 'chat.defaultBackendBadge')
                        : null}
                      language={language}
                      thinkingExpanded={expandedThinking.has(msg.id)}
                      expandedCategories={expandedCategories}
                      onToggleCategory={toggleCategory}
                      onFollowUp={handleFollowUpQuestion}
                      clarification={msg.id === activeClarificationMessageId ? msg.stockClarification ?? null : null}
                      clarificationAutoConfirmEnabled={clarificationAutoConfirmEnabled}
                      copied={copiedMessages.has(msg.id)}
                      copyLabel={copiedMessages.has(msg.id) ? text.copied : text.copy}
                      exportLabel={t('chat.exportMessage')}
                      onCopy={copyMessageToClipboard}
                      onExport={downloadMessageAsMarkdown}
                      onRequestComposerFocus={focusComposer}
                      onClarificationDecision={handleMessageClarificationDecision}
                    />
                  );
                })}
                </>
              )}

              {intakeError && !intakeDraft && (
                <div className="chat-intake chat-intake-error" role="alert" data-testid="chat-intake-error">
                  <span>{intakeError === 'intake_capability_missing'
                    ? t('chat.intake.legacyApi') : intakeError}</span>
                  <div className="chat-intake-error-actions">
                    <button type="button" onClick={() => {
                      const pending = useAgentChatStore.getState().intakeSendPayload;
                      if (pending) {
                        const hints = extractIntentHints(pending.message, stockIndex);
                        void createIntake(pending.message, pending, undefined, hints);
                      }
                    }}>{t('chat.intake.retry')}</button>
                    <button type="button" onClick={() => {
                      const pending = useAgentChatStore.getState().intakeSendPayload;
                      clearIntake();
                      if (pending) void startStream(pending);
                    }}>{t('chat.intake.direct')}</button>
                  </div>
                </div>
              )}

              {loading && (
                <ChatLiveProgress
                  steps={progressSteps}
                  streamingContent={streamingContent}
                  language={language}
                />
              )}

              <div ref={messagesEndRef} />
            </div>
          </ScrollArea>

          {showJumpToBottom && (
            <div className="chat-jump-wrap">
              <button
                type="button"
                className="chat-jump-btn"
                onClick={() => {
                  requestScrollToBottom('smooth');
                  scrollToBottom('smooth');
                }}
                aria-label="查看最新消息"
              >
                <ChevronDown size={14} aria-hidden="true" />
                有新消息
              </button>
            </div>
          )}

        <ChatInputArea
          t={t}
          composerRef={composerRef}
          input={input}
          onInputChange={setInput}
          onSend={() => { void handleSend(); }}
          onStop={() => { void stopStream(); }}
          placeholder={intakeDraft ? t('chat.intake.composerHint') : t('chat.composerPlaceholder')}
          disabled={loading || !agentAvailable || intakeBusy || reportDraftBlocked}
          loading={loading}
          advancedOptionsOpen={advancedOptionsOpen}
          onToggleAdvancedOptions={() => setAdvancedOptionsOpen((open) => !open)}
          selectedSkillSummary={selectedSkillSummary}
          skills={skills}
          selectedSkillIds={selectedSkillIds}
          skillLimitReached={skillLimitReached}
          showSkillDesc={showSkillDesc}
          onShowSkillDesc={setShowSkillDesc}
          onToggleSkill={toggleSkillSelection}
          onClearSkills={() => setSelectedSkillIds([])}
          contextCompressionEnabled={contextCompressionEnabled}
          contextCompressionLoaded={contextCompressionLoaded}
          contextCompressionSaving={contextCompressionSaving}
          contextCompressionError={contextCompressionError}
          onToggleContextCompression={(enabled) => { void updateContextCompressionEnabled(enabled); }}
          activeStockCode={activeStockCode}
          canToggleWatchlist={activeStockContext?.asset_type === 'stock'}
          watchlistBusy={isWatchlistActioning}
          inWatchlist={activeStockCode ? stockInWatchlist(activeStockCode) : false}
          onToggleWatchlist={() => { if (activeStockCode) void handleToggleWatchlist(activeStockCode); }}
          watchlistMessage={watchlistMessage}
          objectStatusText={objectStatusText}
        >
              {intakeBusy && (
                <div className="chat-intake-composer-hint" role="status" data-testid="chat-intake-pending">
                  {t('chat.intake.pending')}
                </div>
              )}
              {intakeDraft && (
                <div className="chat-intake-composer-hint" role="status">
                  {t('chat.intake.composerHint')}
                </div>
              )}
              {legacyIntakeNotice && (
                <div role="status" className="chat-notice is-muted">{t('chat.intake.legacyApi')}</div>
              )}
              {chatError && !reportChatErrorSuppressed ? (
                <ApiErrorAlert error={chatError.code === 'backend_removed'
                  ? { ...chatError, title: t('settings.agentBackendRemovedLabel'), message: t('settings.agentBackendRemoved') }
                  : chatError} />
              ) : null}
              {chatError?.code === 'backend_removed' && (
                <Button variant="secondary" size="sm" onClick={() => navigate('/settings?category=agent')}>
                  {t('chat.openAgentSettings')}
                </Button>
              )}
              {chatError?.code === 'session_state_conflict' && (
                <Button variant="secondary" size="xsm" onClick={() => void refreshSession()}>
                  {t('chat.refreshSession')}
                </Button>
              )}
              {terminalStatus === 'timeout' ? (
                <div role="status" className="chat-notice">
                  {t('chat.analysisTimedOut')}
                </div>
              ) : null}
              {terminalStatus === 'stopped' && (
                <div role="status" className="chat-notice is-muted">{t('chat.stoppedNotice')}</div>
              )}
              {terminalStatus === 'interrupted' && (
                <div role="status" className="chat-notice is-muted">{t('chat.interruptedNotice')}</div>
              )}
              {agentUnavailableError ? (
                <div className="space-y-2">
                  <ApiErrorAlert
                    error={agentUnavailableError}
                    actionLabel={t('chat.openAgentSettings')}
                    onAction={() => navigate('/settings?category=agent')}
                  />
                  <Button variant="secondary" size="sm" onClick={() => void loadAgentStatus()}>
                    {t('chat.recheckAgentStatus')}
                  </Button>
                </div>
              ) : null}
              {agentStatusError ? (
                <InlineAlert
                  variant="warning"
                  title={t('chat.statusUnavailableTitle')}
                  message={t('chat.statusUnavailableMessage')}
                  action={(
                    <Button variant="secondary" size="sm" onClick={() => void loadAgentStatus()}>
                      {t('chat.recheckAgentStatus')}
                    </Button>
                  )}
                  className="chat-inline-notice"
                />
              ) : null}
              {agentStatusChecking ? (
                <InlineAlert
                  variant="info"
                  title={t('chat.statusCheckingTitle')}
                  message={t('chat.statusCheckingMessage')}
                  className="chat-inline-notice"
                />
              ) : null}
              {reportDraft ? (
                <ReportFollowUpCard
                  state={reportDraft.status}
                  recordId={reportDraft.recordId}
                  stockCode={reportDraft.stockCode || reportDraft.requestStockCode}
                  stockName={reportDraft.stockName ?? reportDraft.requestStockName}
                  createdAt={reportDraft.createdAt}
                  error={reportDraft.error}
                  onRetry={reportDraft.status === 'failed' ? handleRetryReportDraft : undefined}
                  onRemove={handleRemoveReportDraft}
                />
              ) : activeReportContext && !pendingReportRemoval ? (
                <ReportFollowUpCard
                  state={activeReportContext.status === 'available' ? 'active' : 'unavailable'}
                  recordId={activeReportContext.record_id}
                  stockCode={activeReportContext.stock_code}
                  stockName={activeReportContext.stock_name}
                  createdAt={activeReportContext.created_at}
                  onRetry={activeReportContext.status === 'available' ? undefined : handleRetryActiveReport}
                  onRemove={handleRemoveActiveReport}
                />
              ) : null}
              {pendingReportRemoval && (
                <div role="status" className="chat-notice is-muted" data-testid="chat-report-removed-notice">
                  {t('chat.reportRemovedNotice')}
                </div>
              )}
              {followUpEvidenceError ? (
                <InlineAlert variant="danger" title="扫描证据未载入" message={followUpEvidenceError} className="chat-inline-notice" />
              ) : null}
              {sendToast ? (
                <InlineAlert
                  variant={sendToast.type === 'success' ? 'success' : 'danger'}
                  title={sendToast.type === 'success' ? '发送成功' : '发送失败'}
                  message={sendToast.message}
                  className="chat-inline-notice"
                />
              ) : null}

        </ChatInputArea>
        </div>
      </section>
    </div>
  );
};

export default ChatPage;
