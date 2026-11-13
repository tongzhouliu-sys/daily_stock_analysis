import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowUp,
  ArrowUpRight,
  Check,
  ChevronDown,
  Copy,
  Download,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Plus,
  Send,
  SlidersHorizontal,
  Sparkles,
  Square,
  Star,
  Trash2,
  X,
} from 'lucide-react';
import { cn } from '../utils/cn';
import { agentApi } from '../api/agent';
import { systemConfigApi } from '../api/systemConfig';
import { ApiErrorAlert, Badge, Button, ConfirmDialog, InlineAlert, ScrollArea } from '../components/common';
import { createParsedApiError, getParsedApiError } from '../api/error';
import type { AgentStatusResponse, SkillInfo } from '../api/agent';
import { DashboardStateBlock } from '../components/dashboard';
import {
  useAgentChatStore,
  type Message,
  type ProgressStep,
  type StockClarificationIdentity,
} from '../stores/agentChatStore';
import StockClarificationCard from '../components/chat/StockClarificationCard';
import ChatSmartSummaryCard from '../components/chat/ChatSmartSummaryCard';
import ChatAnswer from '../components/chat/ChatAnswer';
import { downloadSessionInFormat, formatSessionAsMarkdown, type ExportFormat } from '../utils/chatExport';
import { buildScannerFollowUpPrompt } from '../utils/scannerFollowUp';
import type { ChatFollowUpContext } from '../utils/chatFollowUp';
import {
  buildFollowUpPrompt,
  parseFollowUpRecordId,
  resolveChatFollowUpContext,
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

const isStageDoneSuccessful = (status?: string): boolean => {
  if (!status) return true;
  const normalized = status.trim().toLowerCase();
  return ['completed', 'success', 'succeeded', 'done'].includes(normalized);
};

const getStageDoneLabel = (step: ProgressStep): string => {
  const stage = step.stage || 'stage';
  if (step.message) return step.message;
  if (isStageDoneSuccessful(step.status)) return `${stage} completed`;
  return `${stage} ${step.status || 'finished'}`;
};

const getPipelineBudgetSkippedLabel = (step: ProgressStep): string => {
  if (step.message) return step.message;
  return `${step.stage || 'pipeline'} skipped: insufficient budget`;
};

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
  const [isFollowUpContextLoading, setIsFollowUpContextLoading] = useState(false);
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

  const {
    messages,
    selectedSkillIds: sessionSelectedSkillIds,
    activeStockContext,
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
    setSelectedSkillIds,
    loadSessions,
    loadInitialSession,
    switchSession,
    refreshSession,
    startStream,
    stopStream,
    clearCompletionBadge,
  } = useAgentChatStore();
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
      if (messages.length > 0 || progressSteps.length > 0 || loading) {
        setShowJumpToBottom(true);
      }
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      scrollToBottom(behavior);
      pendingScrollBehaviorRef.current = loading ? 'auto' : 'smooth';
    });

    return () => window.cancelAnimationFrame(frame);
  }, [messages, progressSteps, loading, sessionId, scrollToBottom]);

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
  const selectedSkillIdSet = new Set(selectedSkillIds);
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

  const handleStartNewChat = useCallback(() => {
    followUpContextRef.current = null;
    setSessionMenu(null);
    closeTitleEditor();
    requestScrollToBottom('auto');
    useAgentChatStore.getState().startNewChat();
    setSidebarOpen(false);
  }, [closeTitleEditor, requestScrollToBottom]);

  const handleSwitchSession = useCallback((targetSessionId: string) => {
    setSessionMenu(null);
    closeTitleEditor();
    if (targetSessionId === sessionId) {
      setSidebarOpen(false);
      return;
    }
    followUpContextRef.current = null;
    requestScrollToBottom('auto');
    switchSession(targetSessionId);
    setSidebarOpen(false);
  }, [closeTitleEditor, requestScrollToBottom, sessionId, switchSession]);

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
    followUpContextRef.current = {
      stock_code: stock,
      stock_name: name,
    };
    if (recordId !== undefined || scannerSignal) {
      setIsFollowUpContextLoading(true);
    }
    void Promise.all([resolveChatFollowUpContext({
      stockCode: stock,
      stockName: name,
      recordId,
      index: stockIndex,
    }), scannerSignal
      ? buildScannerFollowUpPrompt(stock, scannerSignal, searchParams.get('scannerRevision'))
      : Promise.resolve(null),
    ]).then(([context, prompt]) => {
      if (!isMountedRef.current || followUpHydrationTokenRef.current !== hydrationToken) {
        return;
      }
      followUpContextRef.current = context;
      if (prompt) setInput((current) => current === initialPrompt ? prompt : current);
    }).catch((error: unknown) => {
      if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
        setFollowUpEvidenceError(error instanceof Error ? error.message : '未能载入冻结证据，请从信号详情重试');
      }
    }).finally(() => {
      if (isMountedRef.current && followUpHydrationTokenRef.current === hydrationToken) {
        setIsFollowUpContextLoading(false);
      }
    });
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, stockIndex, stockIndexLoading, agentStatusChecking]);

  const handleSend = useCallback(
    async (
      overrideMessage?: string,
      overrideSkillIds?: string[],
      overrideStockContext?: ActiveStockContext,
    ) => {
      const msgText = (overrideMessage ?? input).trim();
      if (!msgText || loading || !agentAvailable || !agentStatus || intakeDraft) return;
      if (overrideMessage !== undefined) {
        setInput(msgText);
      }
      const requestedSkillIds = overrideSkillIds ?? sessionSelectedSkillIds;
      const usedSkillIds = normalizeSelectedSkillIds(
        requestedSkillIds ?? selectedSkillIds,
      );
      const usedSkillNames = usedSkillIds.length > 0 ? getSkillNames(usedSkillIds) : ['通用'];
      // Only report/shortcut hints accompany this request. Ordinary follow-ups
      // rely on the server's accepted state, not a replay or a client-selected fact.
      const contextForSend = overrideStockContext ?? followUpContextRef.current ?? undefined;

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
          setIsFollowUpContextLoading(false);
          setInput('');
          requestScrollToBottom('smooth');
        },
      };
      // First valid question of a new session: prepare 1-5 controlled choices.
      // No research, quotes or news lookups start while a draft is pending.
      const isFirstQuestion = messages.length === 0 && hasInitialLoad && !intakeDraft;
      if (isFirstQuestion && guidedIntakeSupported) {
        const hints = extractIntentHints(msgText, stockIndex);
        await createIntake(msgText, payload, streamMeta, hints);
        setInput('');
        requestScrollToBottom('smooth');
        return;
      }
      if (isFirstQuestion) setLegacyIntakeNotice(true);
      await startStream(payload, streamMeta);
    },
    [agentAvailable, agentStatus, createIntake, getSkillNames, guidedIntakeSupported,
      hasInitialLoad, input, intakeDraft, loading, messages.length, normalizeSelectedSkillIds,
      requestScrollToBottom, selectedSkillIds, sessionId, sessionSelectedSkillIds, startStream, stockIndex],
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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

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

  const toggleCategory = (msgId: string, category: string) => {
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
  };

  const copyMessageToClipboard = async (msgId: string, content: string) => {
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
  };

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

  const getCurrentStage = (steps: ProgressStep[]): string => {
    if (steps.length === 0) return '正在连接...';
    const last = steps[steps.length - 1];
    if (last.type === 'thinking') return last.message || 'AI 正在思考...';
    if (last.type === 'tool_start')
      return `${last.display_name || last.tool}...`;
    if (last.type === 'tool_done')
      return `${last.display_name || last.tool} 完成`;
    if (last.type === 'stage_start' || last.type === 'stage_done') {
      const names: Record<string, [string, string]> = {
        technical: ['技术分析 Agent', 'Technical agent'], intel: ['情报 Agent', 'Intelligence agent'],
        risk: ['风险 Agent', 'Risk agent'], decision: ['决策 Agent', 'Decision agent'],
        portfolio: ['组合 Agent', 'Portfolio agent'], agent_loop: ['分析 Agent', 'Analysis agent'],
      };
      const agent = names[last.stage || '']?.[language === 'en' ? 1 : 0] || last.stage || 'Agent';
      if (last.type === 'stage_start') return language === 'en' ? `${agent} is working` : `${agent} 正在分析`;
      const success = isStageDoneSuccessful(last.status);
      return language === 'en' ? `${agent} ${success ? 'completed' : (last.status || 'finished')}` : `${agent} ${success ? '已完成' : (last.status || '已结束')}`;
    }
    if (last.type === 'pipeline_timeout')
      return last.message || `${last.stage || 'pipeline'} timed out`;
    if (last.type === 'pipeline_budget_skipped')
      return getPipelineBudgetSkippedLabel(last);
    if (last.type === 'recovering')
      return language === 'en'
        ? 'Analysis is still running in the background...'
        : '后台任务仍在运行，正在等待最新进度...';
    if (last.type === 'generating')
      return last.message || '正在生成最终分析...';
    return '处理中...';
  };

  const renderThinkingBlock = (msg: Message) => {
    if (!msg.thinkingSteps || msg.thinkingSteps.length === 0) return null;
    
    // Group steps by category
    const categoryMap = new Map<string, ProgressStep[]>();
    const categoryOrder: string[] = [];
    
    msg.thinkingSteps.forEach((step) => {
      // Determine category from step
      let category = 'analysis_steps';
      if (step.type === 'tool_done' || step.type === 'tool_start') {
        const toolName = step.tool || '';
        // Map tool names to categories
        if (toolName.includes('daily_history') || toolName.includes('intraday')) {
          category = 'kline';
        } else if (toolName.includes('stock_info') || toolName.includes('financial')) {
          category = 'fundamental';
        } else if (toolName.includes('realtime_quote')) {
          category = 'quote';
        } else if (toolName.includes('market_indices') || toolName.includes('sector')) {
          category = 'market';
        } else if (toolName.includes('trend') || toolName.includes('ma') || toolName.includes('volume') || toolName.includes('pattern')) {
          category = 'technical';
        } else if (toolName.includes('news')) {
          category = 'news';
        } else if (toolName.includes('intel')) {
          category = 'intel';
        } else {
          category = 'other';
        }
      } else if (step.type === 'stage_start' || step.type === 'stage_done') {
        category = 'agent_collaboration';
      }
      
      if (!categoryMap.has(category)) {
        categoryMap.set(category, []);
        categoryOrder.push(category);
      }
      categoryMap.get(category)!.push(step);
    });
    
    // Category labels
    const categoryLabels: Record<string, [string, string]> = {
      analysis_steps: ['分析步骤', 'Analysis Steps'],
      kline: ['K线获取', 'K-line Data'],
      fundamental: ['基本面分析', 'Fundamental Analysis'],
      quote: ['行情获取', 'Quote Fetch'],
      market: ['行情分析', 'Market Analysis'],
      technical: ['技术分析', 'Technical Analysis'],
      news: ['新闻搜索', 'News Search'],
      intel: ['情报搜索', 'Intel Search'],
      agent_collaboration: ['Agent 协作', 'Agent Collaboration'],
      other: ['工具调用', 'Tool Calls'],
    };
    
    const isExpanded = expandedThinking.has(msg.id);
    
    return (
      <div className="mb-3">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs text-muted-text opacity-60">分析过程</span>
        </div>
        
        {/* Category tags */}
        <div className="flex flex-wrap gap-1.5 mb-2">
          {categoryOrder.map((category) => {
            const steps = categoryMap.get(category) || [];
            const [zhLabel, enLabel] = categoryLabels[category] || ['其他', 'Other'];
            const label = language === 'en' ? enLabel : zhLabel;
            const count = steps.length;
            const isCategoryExpanded = isExpanded && expandedCategories.has(`${msg.id}:${category}`);
            
            return (
              <button
                key={category}
                onClick={() => toggleCategory(msg.id, category)}
                className={cn(
                  'px-2.5 py-1 rounded text-xs transition-all',
                  'border min-w-[48px] min-h-[32px]',
                  isCategoryExpanded
                    ? 'bg-primary/10 border-primary text-primary'
                    : 'bg-muted/50 border-border hover:border-primary/50 text-secondary-text'
                )}
                aria-expanded={isCategoryExpanded}
              >
                <span>{label}</span>
                {count > 0 && (
                  <span className="ml-1 opacity-60">({count})</span>
                )}
              </button>
            );
          })}
        </div>
        
        {/* Expanded category details */}
        {isExpanded && (
          <div className="space-y-3 animate-fade-in">
            {categoryOrder.map((category) => {
              if (!expandedCategories.has(`${msg.id}:${category}`)) return null;
              const steps = categoryMap.get(category) || [];
              
              return (
                <div key={category} className="pl-4 border-l-2 border-primary/30 space-y-1.5">
                  {steps.map((step, idx) => {
                    let statusClass = 'chat-progress-item-muted';
                    let iconClass = 'chat-progress-dot-muted';
                    let text = '';
                    
                    if (step.type === 'tool_done') {
                      const outcome = step.outcome || (step.success ? 'success' : 'failed');
                      const duration = step.duration ? ` · ${step.duration}s` : '';
                      const stockCode = step.detail?.stock_code || '';
                      const prefix = stockCode ? `${stockCode} · ` : '';
                      
                      text = `${prefix}${step.display_label || step.display_name || step.tool}${duration}`;
                      
                      if (outcome === 'success') {
                        statusClass = 'chat-progress-item-success';
                        iconClass = 'chat-progress-dot-success';
                      } else if (outcome === 'timeout') {
                        statusClass = 'chat-progress-item-danger';
                        iconClass = 'chat-progress-dot-danger';
                        text += ' · 超时';
                      } else if (outcome === 'failed') {
                        statusClass = 'chat-progress-item-danger';
                        iconClass = 'chat-progress-dot-danger';
                      } else if (outcome === 'partial') {
                        statusClass = 'chat-progress-item-muted';
                        iconClass = 'chat-progress-dot-muted';
                        text += ' · 部分可用';
                      }
                      
                      // Add detail info if available
                      if (step.detail) {
                        const detailParts: string[] = [];
                        if (step.detail.record_count) {
                          detailParts.push(`${step.detail.record_count} 条记录`);
                        }
                        if (step.detail.date_range) {
                          detailParts.push(String(step.detail.date_range));
                        }
                        if (step.detail.data_source === 'cache') {
                          detailParts.push('历史缓存');
                        }
                        if (detailParts.length > 0) {
                          text += ` · ${detailParts.join(' · ')}`;
                        }
                      }
                    } else if (step.type === 'tool_start') {
                      text = `${step.display_label || step.display_name || step.tool}...`;
                      statusClass = 'chat-progress-item-tool';
                      iconClass = 'chat-progress-dot-tool';
                    } else if (step.type === 'stage_start') {
                      text = step.message || `Starting ${step.stage || 'stage'}...`;
                      statusClass = 'chat-progress-item-thinking';
                      iconClass = 'chat-progress-dot-thinking';
                    } else if (step.type === 'stage_done') {
                      const isSuccess = isStageDoneSuccessful(step.status);
                      text = getStageDoneLabel(step);
                      statusClass = isSuccess ? 'chat-progress-item-success' : 'chat-progress-item-danger';
                      iconClass = isSuccess ? 'chat-progress-dot-success' : 'chat-progress-dot-danger';
                    } else if (step.type === 'pipeline_budget_skipped') {
                      text = getPipelineBudgetSkippedLabel(step);
                      statusClass = 'chat-progress-item-muted';
                      iconClass = 'chat-progress-dot-thinking';
                    } else if (step.type === 'thinking') {
                      text = step.message || `第 ${step.step} 步：思考`;
                      statusClass = 'chat-progress-item-thinking';
                      iconClass = 'chat-progress-dot-thinking';
                    } else if (step.type === 'generating') {
                      text = step.message || '生成分析';
                      statusClass = 'chat-progress-item-generating';
                      iconClass = 'chat-progress-dot-generating';
                    } else {
                      text = step.message || step.type;
                    }
                    
                    return (
                      <div key={idx} className={cn('chat-progress-item', statusClass)}>
                        <span className={cn('chat-progress-dot', iconClass)} />
                        <span className="leading-relaxed">{text}</span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const selectedSkillSummary = selectedSkillIds.length > 0
    ? getSkillNames(selectedSkillIds).join('、')
    : '通用分析';

  const sessionMenuTarget = sessionMenu
    ? sessions.find((session) => session.session_id === sessionMenu.sessionId) ?? null
    : null;

  const sidebarContent = (
    <>
      <div className="chat-sessions-head">
        <button type="button" className="chat-new-btn" onClick={handleStartNewChat}>
          <Plus size={15} aria-hidden="true" />
          <span>{t('chat.newChat')}</span>
        </button>
        <button
          type="button"
          className="chat-icon-btn chat-sidebar-collapse"
          aria-label={t('chat.hideHistory')}
          onClick={toggleSidebar}
        >
          <PanelLeftClose size={16} aria-hidden="true" />
        </button>
      </div>
      <ScrollArea testId="chat-session-list-scroll" className="chat-sessions-scroll" viewportClassName="chat-sessions-viewport">
        {sessionsLoading ? (
          <DashboardStateBlock
            loading
            compact
            title="加载对话中..."
            className="chat-sessions-state"
          />
        ) : sessions.length === 0 ? (
          <DashboardStateBlock
            compact
            title="暂无历史对话"
            description="开始提问后，这里会保留会话记录。"
            className="chat-sessions-state"
          />
        ) : (
          sessionGroups.map((group) => (
            <div key={group.key} className="chat-session-group">
              <p className="chat-session-group-label">{group.label}</p>
              <div className="chat-session-list">
                {group.items.map((s) => (
                  <div key={s.session_id} className="chat-session-row">
                    {editingSessionId === s.session_id && titleEditorOrigin === 'sidebar' ? (
                      <div className="chat-title-editor">
                        <form onSubmit={(event) => { event.preventDefault(); void saveSessionTitle(); }}>
                          <input
                            aria-label={t('chat.editTitle')}
                            value={sessionTitle}
                            maxLength={60}
                            autoFocus
                            onChange={(event) => setSessionTitle(event.target.value)}
                            onKeyDown={(event) => { if (event.key === 'Escape') closeTitleEditor(); }}
                          />
                          <button type="submit" disabled={titleSaving} aria-label={t('chat.saveTitle')}>
                            <Check size={15} aria-hidden="true" />
                          </button>
                          <button type="button" disabled={titleSaving} onClick={closeTitleEditor} aria-label={t('common.cancel')}>
                            <X size={15} aria-hidden="true" />
                          </button>
                        </form>
                        {titleError && <p role="alert">{titleError}</p>}
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => handleSwitchSession(s.session_id)}
                          className={cn('chat-session', s.session_id === sessionId && 'is-active')}
                          aria-label={`切换到对话 ${s.title}`}
                          aria-current={s.session_id === sessionId ? 'page' : undefined}
                        >
                          <span className="chat-session-title">{s.title}</span>
                        </button>
                        <button
                          type="button"
                          className="chat-session-more"
                          aria-label={`${t('chat.moreActions')} ${s.title}`}
                          aria-haspopup="menu"
                          aria-expanded={sessionMenu?.sessionId === s.session_id}
                          onClick={(event) => openSessionMenu(event, s.session_id)}
                        >
                          <MoreHorizontal size={15} aria-hidden="true" />
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </ScrollArea>
    </>
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
                <section className="chat-empty" data-testid="chat-welcome" aria-labelledby="chat-welcome-title">
                  <span className="chat-empty-icon" aria-hidden="true">
                    <Sparkles size={20} />
                  </span>
                  <h3 id="chat-welcome-title" className="chat-empty-title">{t('chat.welcomeTitle')}</h3>
                  <p className="chat-empty-description">{t('chat.emptyDescriptionDefault')}</p>
                  <div className="chat-quick">
                    <span className="chat-quick-label">{t('chat.quickQuestionLabel')}</span>
                    <div className="chat-quick-grid">
                      {quickQuestions.map((q, index) => (
                        <button
                          key={index}
                          type="button"
                          onClick={() => handleQuickQuestion(q)}
                          disabled={!agentAvailable}
                          aria-label={q.label}
                          className="chat-quick-btn"
                        >
                          <span>{q.label}</span>
                          <ArrowUpRight size={14} aria-hidden="true" />
                        </button>
                      ))}
                    </div>
                  </div>
                </section>
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
                  {intakeError && !intakeDraft && (
                    <div className="chat-intake chat-intake-error" role="alert">
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
                  {messages.map((msg) => {
                  const skillLabel = getMessageSkillLabel(msg);
                  return (
                    <div
                      key={msg.id}
                      className={cn('chat-msg', msg.role === 'user' ? 'chat-msg-user' : 'chat-msg-ai')}
                    >
                      {msg.role === 'assistant' && (skillLabel || msg.backend) && (
                        <div className="chat-msg-meta">
                          {skillLabel ? (
                            <Badge variant="info" className="chat-skill-badge shadow-none" aria-label={`技能 ${skillLabel}`}>
                              <Sparkles size={12} aria-hidden="true" />
                              {skillLabel}
                            </Badge>
                          ) : null}
                          {msg.backend ? (
                            <Badge variant={msg.backend === 'codex_app_server' ? 'warning' : 'history'} size="sm">
                              {t(msg.backend === 'codex_app_server' ? 'chat.historicalCodexBackendBadge' : 'chat.defaultBackendBadge')}
                            </Badge>
                          ) : null}
                        </div>
                      )}
                      {msg.role === 'assistant' && renderThinkingBlock(msg)}
                      {msg.role === 'assistant' ? (
                        <>
                          <div className="chat-prose">
                            <ChatAnswer
                              content={msg.content}
                              answerMeta={msg.answerMeta}
                              onFollowUp={(question) => { void handleSend(question); }}
                            />
                          </div>
                          {msg.stockClarification && activeClarificationMessageId === msg.id && (
                            <StockClarificationCard
                              messageId={msg.id}
                              clarification={msg.stockClarification}
                              autoConfirmEnabled={clarificationAutoConfirmEnabled}
                              language={language}
                              onConfirm={(decision, target) => {
                                handleStockClarificationDecision(msg, decision, target);
                              }}
                              onRequestInput={() => composerRef.current?.focus()}
                            />
                          )}
                          <div className="chat-message-actions">
                            <button
                              type="button"
                              onClick={() => copyMessageToClipboard(msg.id, msg.content)}
                              className="chat-action-btn"
                              aria-label={copiedMessages.has(msg.id) ? text.copied : text.copy}
                            >
                              {copiedMessages.has(msg.id)
                                ? <Check size={14} aria-hidden="true" />
                                : <Copy size={14} aria-hidden="true" />}
                            </button>
                            <button
                              type="button"
                              onClick={() => downloadMessageAsMarkdown(msg)}
                              className="chat-action-btn"
                              aria-label={t('chat.exportMessage')}
                            >
                              <Download size={14} aria-hidden="true" />
                            </button>
                          </div>
                        </>
                      ) : (
                        <div className="chat-user-bubble">{msg.content}</div>
                      )}
                    </div>
                  );
                })}
                </>
              )}

              {loading && (
                <div className="chat-msg chat-msg-ai">
                  <div role="status" aria-live="polite" className="chat-live-progress">
                    <div className="chat-live-head">
                      <span className="chat-live-spinner" aria-hidden="true" />
                      <span>{getCurrentStage(progressSteps)}</span>
                    </div>
                    {progressSteps.length > 0 && (
                      <div className="chat-live-details">
                        {progressSteps
                          .filter((step) => ['stage_start', 'stage_done', 'tool_start', 'tool_done', 'generating'].includes(step.type))
                          .slice(-6)
                          .map((step, index) => (
                            <div className="chat-live-step" key={`${index}-${step.type}-${step.stage || step.tool || ''}`}>
                              <span
                                className={cn(
                                  'chat-live-step-dot',
                                  step.type.endsWith('_done') && 'is-done',
                                  ((step.type === 'stage_done' && !isStageDoneSuccessful(step.status))
                                    || (step.type === 'tool_done' && step.success === false)) && 'is-error',
                                )}
                              />
                              <span>{getCurrentStage([step])}</span>
                            </div>
                          ))}
                      </div>
                    )}
                  </div>
                </div>
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

          <div className="chat-composer-area">
            <div className="chat-composer-stack">
              {intakeDraft && (
                <div className="chat-intake-composer-hint" role="status">
                  {t('chat.intake.composerHint')}
                </div>
              )}
              {legacyIntakeNotice && (
                <div role="status" className="chat-notice is-muted">{t('chat.intake.legacyApi')}</div>
              )}
              {chatError ? (
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
              {isFollowUpContextLoading ? (
                <InlineAlert
                  variant="info"
                  title="追问上下文加载中"
                  message="正在加载历史分析上下文；现在可直接发送追问。"
                  className="chat-inline-notice"
                />
              ) : null}
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

              {advancedOptionsOpen && (
                <div id="chat-advanced-options-panel" className="chat-advanced-panel">
                  {skills.length > 0 && (
                    <div
                      id="chat-skill-picker-panel"
                      data-testid="chat-skill-picker-panel"
                      className="chat-skill-picker"
                    >
                      <span className="chat-advanced-label">策略</span>
                      <label className="chat-skill-option">
                        <input
                          type="checkbox"
                          name="general-analysis"
                          value=""
                          checked={selectedSkillIds.length === 0}
                          onChange={() => setSelectedSkillIds([])}
                          className="chat-skill-checkbox"
                        />
                        <span className={cn(selectedSkillIds.length === 0 ? 'is-selected' : '')}>通用分析</span>
                      </label>
                      {skills.map((s) => {
                        const checked = selectedSkillIdSet.has(s.id);
                        const disabled = !checked && skillLimitReached;
                        return (
                          <label
                            key={s.id}
                            className={cn('chat-skill-option', disabled && 'is-disabled')}
                            onMouseEnter={() => setShowSkillDesc(s.id)}
                            onMouseLeave={() => setShowSkillDesc(null)}
                          >
                            <input
                              type="checkbox"
                              name="skills"
                              value={s.id}
                              checked={checked}
                              disabled={disabled}
                              onChange={() => toggleSkillSelection(s.id)}
                              className="chat-skill-checkbox"
                            />
                            <span className={cn(checked ? 'is-selected' : '')}>{s.name}</span>
                            {showSkillDesc === s.id && s.description && (
                              <div className="skill-desc-tooltip">
                                <p className="skill-title">{s.name}</p>
                                <p>{s.description}</p>
                              </div>
                            )}
                          </label>
                        );
                      })}
                    </div>
                  )}
                  <div className="chat-compression-row">
                    <label
                      className={cn(
                        'chat-skill-option',
                        contextCompressionLoaded && !contextCompressionSaving ? '' : 'is-disabled',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={contextCompressionEnabled}
                        disabled={!contextCompressionLoaded || contextCompressionSaving}
                        onChange={(event) => void updateContextCompressionEnabled(event.target.checked)}
                        className="chat-skill-checkbox"
                      />
                      <span className="is-selected">上下文压缩</span>
                      <span className="chat-advanced-hint">节省长会话 token</span>
                    </label>
                    <span className="chat-advanced-status">
                      {contextCompressionSaving
                        ? '保存中...'
                        : contextCompressionEnabled
                          ? '已启用'
                          : '未启用'}
                    </span>
                  </div>
                  {contextCompressionError ? (
                    <InlineAlert
                      variant="danger"
                      title="上下文压缩设置未保存"
                      message={contextCompressionError}
                      className="chat-inline-notice"
                    />
                  ) : null}
                </div>
              )}

              <div className="chat-composer">
                <textarea
                  ref={composerRef}
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={intakeDraft
                    ? t('chat.intake.composerHint') : t('chat.composerPlaceholder')}
                  disabled={loading || !agentAvailable}
                  rows={1}
                  className="chat-composer-input"
                  style={{ height: 'auto' }}
                  onInput={(event) => {
                    const target = event.target as HTMLTextAreaElement;
                    target.style.height = 'auto';
                    target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
                  }}
                />
                <div className="chat-composer-bar">
                  <button
                    type="button"
                    className={cn('chat-chip', advancedOptionsOpen && 'is-active')}
                    onClick={() => setAdvancedOptionsOpen((open) => !open)}
                    aria-label={t('chat.advancedOptions')}
                    aria-expanded={advancedOptionsOpen}
                    aria-controls="chat-advanced-options-panel"
                  >
                    <SlidersHorizontal size={13} aria-hidden="true" />
                    <span className="chat-chip-label">{selectedSkillSummary}</span>
                    <ChevronDown
                      className={cn('chat-chip-chevron', advancedOptionsOpen && 'is-open')}
                      size={13}
                      aria-hidden="true"
                    />
                  </button>

                  {activeStockCode && activeStockContext?.asset_type === 'stock' && (
                    <button
                      type="button"
                      className="chat-chip"
                      disabled={isWatchlistActioning}
                      onClick={() => void handleToggleWatchlist(activeStockCode)}
                    >
                      <Star size={13} aria-hidden="true" />
                      <span>{stockInWatchlist(activeStockCode) ? t('chat.watchlistRemove') : t('chat.watchlistAdd')}</span>
                    </button>
                  )}

                  {watchlistMessage && (
                    <span className="chat-watchlist-message">{watchlistMessage}</span>
                  )}

                  <span className="chat-composer-spacer" />

                  {loading ? (
                    <button
                      type="button"
                      className="chat-stop-btn"
                      onClick={() => void stopStream()}
                      aria-label={t('chat.stop')}
                    >
                      <Square size={12} fill="currentColor" aria-hidden="true" />
                      {t('chat.stop')}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="chat-send-btn"
                      onClick={() => handleSend()}
                      disabled={!input.trim() || loading || !agentAvailable}
                      aria-label="发送"
                    >
                      <ArrowUp size={16} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </div>
              <div className="chat-composer-footer">
                <span className="chat-object-status" aria-label={t('chat.currentObjectLabel')}>
                  {objectStatusText}
                </span>
                <span className="chat-disclaimer">
                  {t('chat.generatedDisclaimer')}
                  <span className="chat-disclaimer-separator" aria-hidden="true">·</span>
                  {t('chat.composerHint')}
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
};

export default ChatPage;
