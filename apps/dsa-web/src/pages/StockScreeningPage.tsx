import type React from 'react';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Bookmark,
  Building2,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  Droplet,
  Factory,
  Flame,
  Gem,
  Landmark,
  Pickaxe,
  Plane,
  Play,
  RefreshCw,
  Search,
  Shield,
  SlidersHorizontal,
  Stethoscope,
  Trees,
  Utensils,
  Wrench,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import {
  screeningApi,
  type ScreeningCandidate,
  type ScreeningExplanationItem,
  type ScreeningHotspotDetail,
  type ScreeningHotspot,
  type ScreeningHotspotsResponse,
  type ScreeningRunSummary,
  type ScreeningScreenResponse,
  type ScreeningScreenTaskStatus,
  type ScreeningStrategy,
} from '../api/screening';
import { formatParsedApiError, getParsedApiError, toApiErrorMessage, type ParsedApiError } from '../api/error';
import { AppPage, Button, InlineAlert, Select } from '../components/common';
import { useUiLanguage, type TranslateFn } from '../contexts/UiLanguageContext';
import type { UiLanguage, UiTextKey } from '../i18n/uiText';

const MARKETS = [{ id: 'cn' }];
const SCREEN_TASK_STORAGE_KEY = 'dsa.screening.activeScreenTask.v1';
const SCREEN_TASK_POLL_INTERVAL_MS = 2000;
const CUSTOM_STRATEGY_OPTION_VALUE = '__custom_strategy__';
const STRATEGY_CATEGORY_KEYS: Record<string, UiTextKey> = {
  framework: 'screening.category.framework',
  income: 'screening.category.income',
  momentum: 'screening.category.momentum',
  quality: 'screening.category.quality',
  reversal: 'screening.category.reversal',
  trend: 'screening.category.trend',
  value: 'screening.category.value',
};

const formatStrategyCategory = (value: string | undefined, t: TranslateFn) => {
  const normalized = value?.trim();
  if (!normalized) {
    return t('screening.category.custom');
  }
  const key = STRATEGY_CATEGORY_KEYS[normalized.toLowerCase()];
  return key ? t(key) : normalized;
};

const dateLocale = (language: UiLanguage) => (language === 'en' ? 'en-US' : 'zh-CN');

type PersistedScreenTask = {
  taskId: string;
  runId?: string;
  market: string;
  strategy: string;
  maxResults: number;
};

const formatRunCreatedAt = (
  value: string | null | undefined,
  language: UiLanguage,
  t: TranslateFn,
) => {
  if (!value) {
    return t('screening.time.unknown');
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString(dateLocale(language), {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
};

// 历史条目里展示筛选条件：策略 ID → 当前语言名称（找不到时回退到原始 ID）
const formatHistoryStrategyName = (
  strategyId: string,
  strategies: ScreeningStrategy[],
  t: TranslateFn,
): string => {
  const matched = strategies.find((item) => item.id === strategyId);
  return matched?.name || matched?.title || strategyId || t('screening.strategy.unknown');
};

// 历史条目里展示筛选条件：市场 ID → 当前语言标签
const formatHistoryMarketLabel = (marketId: string | null | undefined, t: TranslateFn): string => {
  if (!marketId || marketId === 'cn') {
    return t('screening.market.cn');
  }
  return marketId;
};

const readPersistedScreenTask = (): PersistedScreenTask | null => {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(SCREEN_TASK_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<PersistedScreenTask>;
    if (typeof parsed.taskId !== 'string' || !parsed.taskId.trim()) {
      return null;
    }
    const restoredMaxResults = Number(parsed.maxResults);
    return {
      taskId: parsed.taskId,
      runId: typeof parsed.runId === 'string' && parsed.runId.trim() ? parsed.runId : undefined,
      market: typeof parsed.market === 'string' && parsed.market.trim() ? parsed.market : 'cn',
      strategy: typeof parsed.strategy === 'string' && parsed.strategy.trim() ? parsed.strategy : 'dual_low',
      maxResults: Number.isFinite(restoredMaxResults) ? Math.min(100, Math.max(1, restoredMaxResults)) : 3,
    };
  } catch {
    return null;
  }
};

const persistScreenTask = (task: PersistedScreenTask) => {
  try {
    window.sessionStorage.setItem(SCREEN_TASK_STORAGE_KEY, JSON.stringify(task));
  } catch {
    // Session storage is best-effort; polling still works while the page stays mounted.
  }
};

const clearPersistedScreenTask = () => {
  try {
    window.sessionStorage.removeItem(SCREEN_TASK_STORAGE_KEY);
  } catch {
    // Ignore storage cleanup failures.
  }
};

const isUnrecoverableScreenTaskError = (error: ParsedApiError) =>
  error.title === '选股任务不可恢复';

const formatRecoverableScreenTaskPollingError = (error: ParsedApiError, t: TranslateFn) => {
  if (error.category === 'upstream_timeout') {
    return t('screening.task.pollTimeout');
  }
  if (error.category === 'upstream_network' || error.category === 'local_connection_failed') {
    return t('screening.task.pollDisconnected');
  }
  return formatParsedApiError(error) || t('screening.task.pollFailed');
};

const formatScore = (score: ScreeningCandidate['score']) => {
  if (score == null || Number.isNaN(Number(score))) {
    return '-';
  }
  return Number(score).toFixed(2);
};

const formatNumber = (value: unknown, digits = 2) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return Number(value).toFixed(digits);
};

const formatAmount = (value: unknown, language: UiLanguage) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  const amount = Number(value);
  if (language === 'en') {
    if (Math.abs(amount) >= 1_000_000_000) {
      return `${(amount / 1_000_000_000).toFixed(2)}B`;
    }
    if (Math.abs(amount) >= 1_000_000) {
      return `${(amount / 1_000_000).toFixed(2)}M`;
    }
    return amount.toFixed(2);
  }
  if (Math.abs(amount) >= 100_000_000) {
    return `${(amount / 100_000_000).toFixed(2)} 亿`;
  }
  if (Math.abs(amount) >= 10_000) {
    return `${(amount / 10_000).toFixed(2)} 万`;
  }
  return amount.toFixed(2);
};

const formatPercent = (value: unknown) => {
  if (value == null || value === '' || Number.isNaN(Number(value))) {
    return '-';
  }
  return `${(Number(value) * 100).toFixed(0)}%`;
};

const FACTOR_LABEL_KEYS: Record<string, UiTextKey> = {
  value: 'screening.factor.value',
  liquidity: 'screening.factor.liquidity',
  momentum: 'screening.factor.momentum',
  reversal: 'screening.factor.reversal',
  activity: 'screening.factor.activity',
  stability: 'screening.factor.stability',
  size: 'screening.factor.size',
  theme_heat: 'screening.factor.themeHeat',
  topic_alignment: 'screening.factor.topicAlignment',
};

const HOTSPOT_QUALITY_LABEL_KEYS: Record<string, UiTextKey> = {
  available: 'screening.hotspot.quality.available',
  failed: 'screening.hotspot.quality.failed',
  partial: 'screening.hotspot.quality.partial',
  stale: 'screening.hotspot.quality.stale',
};

const HOTSPOT_STAGE_LABEL_KEYS: Record<string, UiTextKey> = {
  accelerating: 'screening.hotspot.stage.accelerating',
  cooling: 'screening.hotspot.stage.cooling',
  diverging: 'screening.hotspot.stage.diverging',
  initial: 'screening.hotspot.stage.initial',
  persistent_hot: 'screening.hotspot.stage.persistent',
  warming: 'screening.hotspot.stage.persistent',
  weakening: 'screening.hotspot.stage.weakening',
};

const HOTSPOT_ROLE_LABEL_KEYS: Record<string, UiTextKey> = {
  core_leader: 'screening.hotspot.role.coreLeader',
  follower: 'screening.hotspot.role.follower',
  laggard: 'screening.hotspot.role.laggard',
  leader: 'screening.hotspot.role.coreLeader',
  secondary: 'screening.hotspot.role.secondary',
};

const HOTSPOT_MISSING_FIELD_LABEL_KEYS: Record<string, UiTextKey> = {
  canonical_topic: 'screening.hotspot.missingField.canonicalTopic',
  hotspot_constituents: 'screening.hotspot.missingField.hotspotConstituents',
  leader_stocks: 'screening.hotspot.missingField.leaderStocks',
  live_stocks: 'screening.hotspot.missingField.liveStocks',
  route: 'screening.hotspot.missingField.route',
  source: 'screening.hotspot.missingField.source',
  stocks: 'screening.hotspot.missingField.stocks',
  timeline: 'screening.hotspot.missingField.timeline',
};

const labelFrom = (keys: Record<string, UiTextKey>, value: unknown, t: TranslateFn) => {
  const text = String(value || '').trim();
  const key = keys[text.toLowerCase()];
  return key ? t(key) : text;
};

const getHotspotStageLabel = (value: unknown, t: TranslateFn) =>
  labelFrom(HOTSPOT_STAGE_LABEL_KEYS, value, t);

const getHotspotRoleLabel = (value: unknown, t: TranslateFn) =>
  labelFrom(HOTSPOT_ROLE_LABEL_KEYS, value, t) || t('screening.hotspot.role.default');

const getHotspotQualityLabel = (value: unknown, t: TranslateFn) =>
  labelFrom(HOTSPOT_QUALITY_LABEL_KEYS, value, t) || t('screening.hotspot.quality.pending');

const getCandidateReason = (item: ScreeningCandidate, t: TranslateFn) => {
  if (item.llmThesis || item.llmScore != null) {
    return item.reason || item.llmThesis || t('screening.candidate.defaultReason');
  }
  if (item.reason) {
    return item.reason;
  }
  const summaries = item.postAnalysisSummaries || {};
  const summary = Object.values(summaries).find((value) => typeof value === 'string' && value.trim());
  if (typeof summary === 'string') {
    return summary;
  }
  return t('screening.candidate.noSummary');
};

const getSignal = (item: ScreeningCandidate, t: TranslateFn) => {
  const rawSignal = item.raw.action ?? item.raw.signal ?? item.raw.recommendation;
  return typeof rawSignal === 'string' && rawSignal.trim() ? rawSignal : t('screening.signal.watch');
};

const getFactorEntries = (item: ScreeningCandidate) =>
  Object.entries(item.factorScores || {})
    .filter(([, value]) => typeof value === 'number')
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .slice(0, 6);

const getSelectionExplanations = (
  item: ScreeningCandidate,
  t: TranslateFn,
): ScreeningExplanationItem[] => {
  if (item.whySelected?.length) return item.whySelected;
  // Legacy runs lack provenance. Retain every stored summary as unknown rather
  // than re-scoring historical factors or asserting it was observed.
  const summaries = Array.from(new Set(
    [item.reason, item.llmThesis, ...Object.values(item.postAnalysisSummaries || {})]
      .filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
      .map((value) => value.trim()),
  ));
  return summaries.map((summary) => ({
    code: 'legacy_summary',
    text: t('screening.candidate.legacySummary', { summary }),
    source: 'legacy_result',
    quality: 'unknown',
  }));
};

const ExplanationItems = ({ items, emptyText }: { items?: ScreeningExplanationItem[]; emptyText: string }) => {
  const { t } = useUiLanguage();
  return items?.length ? (
    <ul className="mt-2 space-y-2">
      {items.map((item, index) => (
        <li key={`${item.code}-${item.source}-${index}`} className="rounded-lg border border-border/50 bg-background/30 p-2">
          <p className="text-sm leading-6 text-foreground">{item.text}</p>
          <p className="mt-1 text-xs text-secondary-text">
            {t('screening.candidate.sourceLine', {
              source: item.source || 'unknown',
              quality: item.quality || 'unknown',
            })}
          </p>
        </li>
      ))}
    </ul>
  ) : <p className="mt-1 text-sm leading-6 text-foreground">{emptyText}</p>;
};

const toMessageList = (values: string[] | undefined) =>
  Array.isArray(values) ? values.map((value) => String(value).trim()).filter(Boolean) : [];

const KNOWN_SNAPSHOT_SOURCES = new Set(['tushare', 'sina', 'efinance', 'akshare_em', 'em_datacenter', 'baostock']);
const MAX_MESSAGE_DETAIL_LENGTH = 96;

const truncateMessageDetail = (value: string, maxLength = MAX_MESSAGE_DETAIL_LENGTH) => {
  const text = value.replace(/\s+/g, ' ').trim();
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}…`;
};

const summarizeScreeningDiagnostic = (detail: string, t: TranslateFn) => {
  if (/no_json_found|invalid_response|coverage below threshold/i.test(detail)) {
    return t('screening.llm.diagnostic.noStructured');
  }
  if (/call_failed/i.test(detail)) {
    return t('screening.llm.diagnostic.callFailed');
  }
  if (/trade_cal returned no open trading days/i.test(detail)) {
    return t('screening.llm.diagnostic.noTradingDays');
  }
  if (/too many requests|rate limit|http\s*429/i.test(detail)) {
    return t('screening.llm.diagnostic.rateLimited');
  }
  if (/403 forbidden|forbidden|access denied/i.test(detail)) {
    return t('screening.llm.diagnostic.forbidden');
  }
  if (/timeout|timed out/i.test(detail)) {
    return t('screening.llm.diagnostic.timeout');
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(detail)) {
    return t('screening.llm.diagnostic.network');
  }
  if (/missing .*api key|GEMINI_API_KEY|GOOGLE_API_KEY|gemini_api_key/i.test(detail)) {
    return t('screening.llm.diagnostic.missingKey');
  }
  if (/returned no data|empty/i.test(detail)) {
    return t('screening.llm.diagnostic.empty');
  }

  const withoutUrl = detail
    .replace(/https?:\/\/\S+/gi, 'URL')
    .replace(/\bwith url:\s*\S+/gi, 'with url: URL')
    .replace(/\burl:\s*\S+/gi, 'url: URL');
  return truncateMessageDetail(withoutUrl);
};

const parseSourceDiagnostic = (value: string) => {
  const match = value.match(/^([a-zA-Z0-9_-]+)\s*[:：]\s*(.+)$/);
  if (!match) {
    return null;
  }
  return {
    source: match[1],
    detail: match[2],
  };
};

const normalizeScreenMessageKey = (value: string, t: TranslateFn) => {
  const formatted = formatScreenMessage(value, t);
  return formatted ? formatted.trim().toLowerCase() : value.trim().toLowerCase();
};

const formatEnrichmentSummary = (value: string, t: TranslateFn) =>
  value
    .replace(/DSA行情\s*[:：]\s*/gi, t('screening.enrich.quote'))
    .replace(/DSA新闻\s*[:：]\s*/gi, t('screening.enrich.news'))
    .replace(/DSA事件\s*[:：]\s*/gi, t('screening.enrich.events'));

const formatScreenMessage = (value: string, t: TranslateFn) => {
  if (/^DSA provider context applied \d+ of \d+ candidates/i.test(value)) {
    return '';
  }
  if (/^LLM ranking skipped:\s*no LLM config/i.test(value)) {
    return t('screening.llm.skipped');
  }
  if (/^LLM ranking failed/i.test(value)) {
    return t('screening.llm.failed', { reason: summarizeScreeningDiagnostic(value, t) });
  }
  if (/no_json_found|invalid_response|coverage below threshold|call_failed/i.test(value)) {
    return t('screening.llm.failed', { reason: summarizeScreeningDiagnostic(value, t) });
  }
  if (/^(?:LLM ranking prompt|LLM context) truncated:/i.test(value)) {
    return '';
  }
  if (/^(?:Remote post-analysis cap|Risk veto excluded|Snapshot hard-filter waterfall|Daily hard-filter waterfall|Daily hard-filter rejections|Candidate context collected rows=)/i.test(value)) {
    return '';
  }
  if (/^Daily K-line (?:enrichment attempted|sources|quality flags|source ordering|source health):?/i.test(value)) {
    return '';
  }
  if (/^Daily K-line enrichment row errors:/i.test(value)) {
    return t('screening.enrich.dailyRowsFailed');
  }
  if (/^Daily K-line enrichment skipped:/i.test(value)) {
    return t('screening.enrich.dailySkipped');
  }
  if (/^Candidate context row errors:/i.test(value)) {
    return t('screening.enrich.contextRowsFailed');
  }
  if (/^Industry\/concepts enrichment:/i.test(value)) {
    return t('screening.enrich.industryFailed');
  }
  if (/^DSA deep analysis failed for /i.test(value)) {
    return t('screening.enrich.deepAnalysisFailed');
  }

  const snapshotFallback = value.match(/^Snapshot source fallback:\s*(.+)$/i);
  if (snapshotFallback) {
    const parsed = parseSourceDiagnostic(snapshotFallback[1]);
    if (parsed) {
      return t('screening.enrich.sourceFallback', {
        source: parsed.source,
        reason: summarizeScreeningDiagnostic(parsed.detail, t),
      });
    }
    return t('screening.enrich.sourceFallbackNoSource', {
      reason: summarizeScreeningDiagnostic(snapshotFallback[1], t),
    });
  }

  const parsed = parseSourceDiagnostic(value);
  if (parsed && KNOWN_SNAPSHOT_SOURCES.has(parsed.source.toLowerCase())) {
    return t('screening.enrich.sourceFallback', {
      source: parsed.source,
      reason: summarizeScreeningDiagnostic(parsed.detail, t),
    });
  }
  return truncateMessageDetail(value);
};

const getScreenMessages = (meta: ScreeningScreenResponse | null, t: TranslateFn) => {
  if (!meta) {
    return [];
  }
  const messages: string[] = [];
  const seen = new Set<string>();
  [...toMessageList(meta.warnings), ...toMessageList(meta.sourceErrors), ...toMessageList(meta.llmParseErrors)].forEach(
    (value) => {
      const key = normalizeScreenMessageKey(value, t);
      if (seen.has(key)) {
        return;
      }
      const message = formatScreenMessage(value, t);
      if (!message) {
        return;
      }
      seen.add(key);
      messages.push(message);
    },
  );
  return messages;
};

const isRunningScreenTask = (status: string | undefined | null) => status === 'pending' || status === 'processing';

const formatScreenTaskFailure = (value: string | null | undefined, t: TranslateFn) => {
  const text = String(value || '').trim();
  if (!text) {
    return t('screening.task.failed');
  }
  return t('screening.task.failedWithReason', {
    reason: summarizeScreeningDiagnostic(text, t),
  });
};

const SCREENING_HOTSPOT_NO_CACHE_HINT = 'No cached Screening hotspot snapshot. Click refresh to fetch live hotspots.';
const SCREENING_HOTSPOT_UNAVAILABLE_CODE = 'eastmoney_hotspot_unavailable';

const formatHotspotEmptyMessage = (result: ScreeningHotspotsResponse, t: TranslateFn) => {
  const message = String(result.message || '').trim();
  const sourceErrors = result.sourceErrors || [];
  if (message && sourceErrors.includes(SCREENING_HOTSPOT_UNAVAILABLE_CODE)) {
    return message;
  }
  if (message === SCREENING_HOTSPOT_NO_CACHE_HINT) {
    return t('screening.hotspot.noCache');
  }
  const sourceError = sourceErrors[0];
  if (sourceError) {
    return t('screening.hotspot.unavailableWithReason', {
      reason: summarizeScreeningDiagnostic(sourceError, t),
    });
  }
  return t('screening.hotspot.unavailable');
};

const ScreenAlertMessage: React.FC<{ messages: string[] }> = ({ messages }) => {
  if (messages.length <= 1) {
    return <span>{messages[0]}</span>;
  }
  return (
    <ul className="list-disc space-y-1 pl-4">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
};

const hasLlmInsight = (item: ScreeningCandidate) =>
  Boolean(
    item.llmThesis ||
      item.llmSector ||
      item.llmTheme ||
      item.llmConfidence != null ||
      item.llmWatchItems?.length ||
      item.llmCatalysts?.length,
  );

const getRiskClassName = (riskLevel: string | undefined) => {
  if (riskLevel === 'high') {
    return 'bg-danger/10 text-danger';
  }
  if (riskLevel === 'medium') {
    return 'bg-warning/10 text-warning';
  }
  if (riskLevel === 'low') {
    return 'bg-success/10 text-success';
  }
  return 'bg-surface text-secondary-text';
};

const getRiskLabel = (riskLevel: string | undefined, t: TranslateFn) => {
  if (riskLevel === 'high') return t('screening.risk.high');
  if (riskLevel === 'medium') return t('screening.risk.medium');
  if (riskLevel === 'low') return t('screening.risk.low');
  return t('screening.risk.unknown');
};

const getRouteTimeLabel = (
  item: ScreeningHotspotDetail['route'][number],
  language: UiLanguage,
  t: TranslateFn,
) => {
  const rawTime = item.publishedAt || item.date || item.time || '';
  if (!rawTime) {
    return t('screening.hotspot.timePending');
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(rawTime)) {
    return rawTime;
  }
  const parsed = new Date(rawTime);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleString(dateLocale(language), {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  }
  return rawTime;
};

const formatHotspotRouteTitle = (value: string, t: TranslateFn) => {
  const text = String(value || '').trim();
  const normalized = text.toLowerCase();
  if (normalized === 'current fermentation') {
    return t('screening.hotspot.route.current');
  }
  if (normalized === 'news catalyst') {
    return t('screening.hotspot.route.news');
  }
  return text || t('screening.hotspot.route.fallback');
};

const formatHotspotRouteDescription = (value: string, t: TranslateFn) => {
  const text = String(value || '').trim();
  if (!text) {
    return t('screening.hotspot.route.empty');
  }
  const parts = text.split(/\s*;\s*/).map((part) => part.trim()).filter(Boolean);
  if (parts.some((part) => /\b(?:heat|stage|leaders?)\b/i.test(part))) {
    const localized = parts.map((part) => {
      const heat = part.match(/^(.*?)\s+heat\s+(-?\d+(?:\.\d+)?)$/i);
      if (heat) {
        return t('screening.hotspot.routeHeat', { name: heat[1], value: formatNumber(heat[2], 1) });
      }
      const stage = part.match(/^stage\s+(.+)$/i);
      if (stage) {
        return t('screening.hotspot.summaryStage', { stage: getHotspotStageLabel(stage[1], t) });
      }
      const leaders = part.match(/^leaders?\s+(.+)$/i);
      if (leaders) {
        return t('screening.hotspot.summaryLeaders', {
          leaders: leaders[1].split(/\s*,\s*/).filter(Boolean).join(', '),
        });
      }
      return part;
    });
    return `${localized.join(t('screening.clauseSeparator'))}${t('screening.sentenceEnd')}`;
  }
  if (/Dsa[A-Z]|Provider\b|stock_board_|concept_constituents|leader_stocks|last_good_cache/i.test(text)) {
    return t('screening.hotspot.route.changed');
  }
  return text;
};

const getHotspotMissingFieldLabels = (values: string[] | undefined, t: TranslateFn) => {
  const labels = (values || []).map((value) =>
    labelFrom(HOTSPOT_MISSING_FIELD_LABEL_KEYS, value, t) || t('screening.hotspot.partialDetail'),
  );
  return [...new Set(labels)];
};

const formatHotspotDiagnostic = (value: string, t: TranslateFn) => {
  const text = String(value || '').trim();
  const timeoutSeconds = text.match(/timed out after\s*(\d+(?:\.\d+)?)s/i);
  if (timeoutSeconds) {
    return t('screening.hotspot.diagnostic.timeoutSeconds', { seconds: timeoutSeconds[1] });
  }
  if (/timeout|timed out/i.test(text)) {
    return t('screening.hotspot.diagnostic.timeout');
  }
  if (/RemoteDisconnected|Connection aborted|ProtocolError|ConnectionPool|Max retries exceeded|ProxyError|NameResolutionError/i.test(text)) {
    return t('screening.hotspot.diagnostic.network');
  }
  if (/eastmoney_hotspot_unavailable|returned no data|no live hotspot rows|\bempty\b/i.test(text)) {
    return t('screening.hotspot.diagnostic.noData');
  }
  if (/rate limit|too many requests|http\s*429/i.test(text)) {
    return t('screening.hotspot.diagnostic.rateLimited');
  }
  if (/Dsa[A-Z]|Provider\b|stock_board_|concept_constituents|leader_stocks|last_good_cache|^[a-z0-9_.:-]+$/i.test(text)) {
    return t('screening.hotspot.diagnostic.partial');
  }
  if (/[\u0080-\uFFFF]/.test(text)) {
    return truncateMessageDetail(text);
  }
  return t('screening.hotspot.diagnostic.partial');
};

const getHotspotDiagnosticMessages = (values: string[] | undefined, t: TranslateFn) =>
  [...new Set((values || []).map((value) => formatHotspotDiagnostic(value, t)).filter(Boolean))].slice(0, 4);

const hasHotspotDetailDegradation = (detail: ScreeningHotspotDetail) => {
  if ((detail.missingFields || []).length > 0) {
    return true;
  }
  const qualityStatus = String(detail.qualityStatus || '').trim().toLowerCase();
  if (qualityStatus) {
    return qualityStatus !== 'available';
  }
  return (detail.sourceErrors || []).length > 0;
};

const getHotspotFallbackLabel = (detail: ScreeningHotspotDetail, t: TranslateFn) => {
  if (detail.stale || detail.cacheUsed) {
    return detail.staleAgeHours != null
      ? t('screening.hotspot.fallback.cacheHours', { hours: formatNumber(detail.staleAgeHours, 1) })
      : t('screening.hotspot.fallback.cache');
  }
  return t('screening.hotspot.fallback.backup');
};

const getHotspotSummaryText = (
  detail: ScreeningHotspotDetail,
  hotspot: ScreeningHotspot | undefined,
  t: TranslateFn,
) => {
  const summaryDetail = detail.summaryDetail || {};
  const heatScore = summaryDetail.heatScore ?? summaryDetail.heat_score ?? hotspot?.heatScore;
  const stage = summaryDetail.stage ?? hotspot?.stage ?? hotspot?.state;
  const rawLeaders = summaryDetail.leaders ?? hotspot?.leaders;
  const leaders = Array.isArray(rawLeaders)
    ? rawLeaders.map((value) => String(value).trim()).filter(Boolean).slice(0, 3)
    : [];
  const parts: string[] = [];
  if (heatScore != null && !Number.isNaN(Number(heatScore))) {
    parts.push(t('screening.hotspot.summaryHeat', { value: formatNumber(heatScore, 1) }));
  }
  if (stage) {
    parts.push(t('screening.hotspot.summaryStage', { stage: getHotspotStageLabel(stage, t) }));
  }
  if (leaders.length > 0) {
    parts.push(t('screening.hotspot.summaryLeaders', {
      leaders: leaders.join(t('screening.listSeparator')),
    }));
  }
  if (parts.length > 0) {
    return t('screening.hotspot.summaryLine', {
      name: detail.name || detail.canonicalTopic || detail.topic,
      parts: parts.join(t('screening.clauseSeparator')),
    });
  }
  const summary = String(detail.summary || '').trim();
  if (summary && !/\b(?:heat|stage|leaders?|quality status|available|partial|stale|failed)\b|Dsa[A-Z]|Provider\b|stock_board_/i.test(summary)) {
    return summary;
  }
  return t('screening.hotspot.loaded');
};

const buildHotspotPreviewDetail = (hotspot: ScreeningHotspot, t: TranslateFn): ScreeningHotspotDetail => {
  const leaders = (hotspot.leaders || []).map((value) => String(value).trim()).filter(Boolean);
  const stage = getHotspotStageLabel(hotspot.stage || hotspot.state, t);
  const descriptionParts = [
    t('screening.hotspot.routeHeat', {
      name: hotspot.name || hotspot.topic,
      value: formatHotspotMetric(hotspot.heatScore, t),
    }),
  ];
  if (stage) {
    descriptionParts.push(t('screening.hotspot.summaryStage', { stage }));
  }
  if (leaders.length > 0) {
    descriptionParts.push(t('screening.hotspot.summaryLeaders', {
      leaders: leaders.slice(0, 3).join(t('screening.listSeparator')),
    }));
  }
  const stocks = (hotspot.leaderStocks || []).slice(0, 10);
  return {
    enabled: true,
    provider: 'akshare',
    topic: hotspot.topic,
    name: hotspot.name || hotspot.topic,
    canonicalTopic: hotspot.topic,
    summaryDetail: {
      heatScore: hotspot.heatScore,
      stage: hotspot.stage || hotspot.state,
      leaders,
    },
    route: [{
      title: t('screening.hotspot.route.current'),
      description: `${descriptionParts.join(t('screening.clauseSeparator'))}${t('screening.sentenceEnd')}`,
    }],
    stocks,
    stockCount: hotspot.sampleStockCount ?? stocks.length,
    sourceErrors: hotspot.sourceErrors,
    qualityStatus: hotspot.qualityStatus,
    missingFields: hotspot.missingFields,
    fallbackUsed: hotspot.fallbackUsed,
    stale: hotspot.stale,
    staleAgeHours: hotspot.staleAgeHours,
    cacheUsed: hotspot.cacheUsed,
    cachedAt: hotspot.cachedAt,
  };
};

const stripHotspotSearchAugmentation = (detail: ScreeningHotspotDetail): ScreeningHotspotDetail => {
  const baseDetail: ScreeningHotspotDetail = {
    ...detail,
    route: (detail.route || []).filter((item) => !item.searchResult),
    ...(detail.timeline
      ? { timeline: detail.timeline.filter((item) => !item.searchResult) }
      : {}),
  };
  delete baseDetail.newsSearchRequested;
  delete baseDetail.newsSearchStatus;
  return baseDetail;
};

const stripHotspotSearchAugmentationByTopic = (
  details: Record<string, ScreeningHotspotDetail>,
) => Object.fromEntries(
  Object.entries(details).map(([topic, detail]) => [topic, stripHotspotSearchAugmentation(detail)]),
) as Record<string, ScreeningHotspotDetail>;

const getHotspotRouteItems = (detail: ScreeningHotspotDetail) => {
  const route = detail.route || [];
  if (route.length > 0) {
    return route;
  }
  return detail.timeline || [];
};

const formatHotspotMetric = (value: unknown, t: TranslateFn, digits = 1) => {
  const formatted = formatNumber(value, digits);
  return formatted === '-' ? t('screening.hotspot.metric.observing') : formatted;
};

const getHotspotLeadersText = (item: ScreeningHotspot, t: TranslateFn) => {
  const leaders = (item.leaders || []).map((value) => String(value).trim()).filter(Boolean);
  if (leaders.length > 0) {
    return leaders.slice(0, 2).join(t('screening.listSeparator'));
  }
  return t('screening.hotspot.metric.observing');
};

const getHotspotSampleText = (item: ScreeningHotspot, t: TranslateFn) => {
  if (item.sampleStockCount == null || Number.isNaN(Number(item.sampleStockCount))) {
    return t('screening.hotspot.sample.observing');
  }
  return t('screening.hotspot.sample.coverage', { count: item.sampleStockCount });
};

const formatStockChangeText = (value: unknown, t: TranslateFn) => {
  const formatted = formatNumber(value);
  return formatted === '-' ? t('screening.hotspot.noQuote') : `${formatted}%`;
};

const formatHotspotUpdatedAt = (value: string | null, language: UiLanguage, t: TranslateFn) => {
  if (!value) {
    return t('screening.hotspot.pendingRefresh');
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString(dateLocale(language), {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
};

const getHotspotStrength = (item: ScreeningHotspot, index: number, t: TranslateFn) => {
  const heat = Number(item.heatScore ?? 0);
  const changePct = Number(item.changePct ?? 0);
  if (index === 0 || heat >= 90 || changePct >= 8) {
    return { label: t('screening.hotspot.strength.leading'), className: 'bg-red-500/10 text-red-500' };
  }
  if (heat >= 80 || changePct >= 5) {
    return { label: t('screening.hotspot.strength.strong'), className: 'bg-blue-500/10 text-blue-500' };
  }
  return { label: t('screening.hotspot.strength.firm'), className: 'bg-cyan/10 text-cyan' };
};

// Provider hot-topic names stay in the source language (mostly Chinese), so the
// icon heuristics intentionally keep matching Chinese keywords in every UI language.
const HOTSPOT_ICON_RULES: Array<{
  pattern: RegExp;
  icon: React.ComponentType<{ className?: string }>;
  className: string;
}> = [
  { pattern: /金|银|铜|铝|铅|锌|钼|钴|镍|贵金属|矿|有色/, icon: Pickaxe, className: 'bg-orange-500/10 text-orange-500' },
  { pattern: /黄金|珠宝/, icon: Gem, className: 'bg-amber-500/10 text-amber-500' },
  { pattern: /油|气|能源|煤/, icon: Droplet, className: 'bg-yellow-700/10 text-yellow-700' },
  { pattern: /金融|券商|银行|保险|资本/, icon: Landmark, className: 'bg-orange-500/10 text-orange-500' },
  { pattern: /航空|机场|航天|运输/, icon: Plane, className: 'bg-blue-500/10 text-blue-500' },
  { pattern: /林业|农业|种植/, icon: Trees, className: 'bg-emerald-500/10 text-emerald-500' },
  { pattern: /医疗|诊断|卫生|医药/, icon: Stethoscope, className: 'bg-teal-500/10 text-teal-500' },
  { pattern: /食品|餐饮|酒/, icon: Utensils, className: 'bg-violet-500/10 text-violet-500' },
  { pattern: /工业|制造|修理|机械|设备/, icon: Wrench, className: 'bg-blue-500/10 text-blue-500' },
  { pattern: /租赁|地产|建筑/, icon: Building2, className: 'bg-emerald-500/10 text-emerald-500' },
  { pattern: /电|芯片|算力|AI|机器人/, icon: Factory, className: 'bg-indigo-500/10 text-indigo-500' },
  { pattern: /保险|安全/, icon: Shield, className: 'bg-blue-500/10 text-blue-500' },
];

const getHotspotIcon = (topic: string) => {
  const match = HOTSPOT_ICON_RULES.find((rule) => rule.pattern.test(topic));
  return match || { icon: Activity, className: 'bg-cyan/10 text-cyan' };
};

const MiniSparkline: React.FC<{ score?: number | null; selected?: boolean }> = ({ score, selected }) => {
  const normalizedScore = Number.isFinite(Number(score)) ? Math.max(0, Math.min(100, Number(score))) : 65;
  const lift = Math.max(0, Math.min(16, normalizedScore / 7));
  const path = `M2 35 C12 ${32 - lift / 4}, 16 ${34 - lift / 2}, 24 ${28 - lift / 3} S38 ${29 - lift}, 46 ${23 - lift / 2} S62 ${24 - lift}, 72 ${16 - lift / 3} S86 ${15 - lift}, 94 ${7}`;
  return (
    <svg className="h-8 w-20" viewBox="0 0 96 40" aria-hidden="true">
      <path d={`${path} L94 40 L2 40 Z`} fill={selected ? 'rgba(249,115,22,0.14)' : 'rgba(59,130,246,0.12)'} />
      <path d={path} fill="none" stroke={selected ? '#f97316' : '#3b82f6'} strokeLinecap="round" strokeWidth="2" />
    </svg>
  );
};

const StockScreeningPage: React.FC = () => {
  const navigate = useNavigate();
  const { language, t } = useUiLanguage();
  const [restoredTask] = useState<PersistedScreenTask | null>(() => readPersistedScreenTask());
  const [statusState, setStatusState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [statusError, setStatusError] = useState('');
  const [statusAttempt, setStatusAttempt] = useState(0);
  const [enabled, setEnabled] = useState(false);
  const [available, setAvailable] = useState(false);
  const [market, setMarket] = useState(restoredTask?.market || 'cn');
  const [strategy, setStrategy] = useState(restoredTask?.strategy || 'dual_low');
  const [strategies, setStrategies] = useState<ScreeningStrategy[]>([]);
  const [maxResults, setMaxResults] = useState(restoredTask?.maxResults || 3);
  const [candidates, setCandidates] = useState<ScreeningCandidate[]>([]);
  const [hotspots, setHotspots] = useState<ScreeningHotspot[]>([]);
  const [hotspotsUpdatedAt, setHotspotsUpdatedAt] = useState<string | null>(null);
  const [hotspotsExpanded, setHotspotsExpanded] = useState(false);
  const [selectedHotspotTopic, setSelectedHotspotTopic] = useState<string | null>(null);
  const selectedHotspotTopicRef = useRef<string | null>(null);
  const hotspotDetailRequestIdRef = useRef(0);
  const hotspotDetailsByTopicRef = useRef<Record<string, ScreeningHotspotDetail>>({});
  const historyRunRequestIdRef = useRef(0);
  const [hotspotDetail, setHotspotDetail] = useState<ScreeningHotspotDetail | null>(null);
  const [loadingHotspotDetail, setLoadingHotspotDetail] = useState(false);
  const [searchingHotspotNews, setSearchingHotspotNews] = useState(false);
  const [hotspotDetailError, setHotspotDetailError] = useState('');
  const [loadingHotspots, setLoadingHotspots] = useState(false);
  const [hotspotError, setHotspotError] = useState('');
  const [screenMeta, setScreenMeta] = useState<ScreeningScreenResponse | null>(null);
  const [expandedCode, setExpandedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(restoredTask?.taskId));
  const [enabling, setEnabling] = useState(false);
  const [historyRuns, setHistoryRuns] = useState<ScreeningRunSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [restoreResolved, setRestoreResolved] = useState(() => !restoredTask?.runId);
  // 标记当前 strategy 是否来自历史 run（刷新自动恢复或手动历史选择）的上下文同步。
  // 为 true 时 loadStrategies 跳过“不在列表则回退第一项”的归一化，
  // 防止迟到的 /strategies 响应把历史上下文改写回默认策略。
  const historyContextStrategyRef = useRef(false);
  const [loadingStrategies, setLoadingStrategies] = useState(false);
  const [error, setError] = useState('');
  const [strategyLoadError, setStrategyLoadError] = useState('');
  const [activeTaskId, setActiveTaskId] = useState<string | null>(restoredTask?.taskId ?? null);
  const activeTaskContextRef = useRef<PersistedScreenTask | null>(restoredTask);
  const [taskProgress, setTaskProgress] = useState(restoredTask?.taskId ? 10 : 0);
  const [taskMessage, setTaskMessage] = useState(
    restoredTask?.taskId ? 'screening.task.restoring' : '',
  );

  const selectedStrategy = useMemo(() => strategies.find((item) => item.id === strategy), [strategies, strategy]);
  const selectedStrategyTitle = selectedStrategy?.name || selectedStrategy?.title || t('screening.strategy.custom');
  const selectedStrategyTag = formatStrategyCategory(
    selectedStrategy?.category || selectedStrategy?.tag || selectedStrategy?.tags?.[0],
    t,
  );
  const displayedStrategy = selectedStrategy
    ? selectedStrategyTitle
    : t('screening.strategy.customWithId', { id: strategy });
  const screenMessages = useMemo(() => getScreenMessages(screenMeta, t), [screenMeta, t]);
  const resolvedTaskMessage = useMemo(() => {
    if (!taskMessage) {
      return '';
    }
    if (taskMessage.startsWith('screening.')) {
      return t(taskMessage as UiTextKey);
    }
    return taskMessage;
  }, [taskMessage, t]);
  const selectedHotspot = useMemo(
    () => hotspots.find((item) => item.topic === selectedHotspotTopic),
    [hotspots, selectedHotspotTopic],
  );
  const factorRanking = Boolean(screenMeta && (screenMeta.rankingMode === 'factor' || screenMeta.llmRanked === false));
  const llmFailed = Boolean(factorRanking && screenMeta?.llmFailureReason);
  const alertMessages = llmFailed
    ? screenMessages.length > 0
      ? screenMessages
      : [t('screening.llm.pending')]
    : screenMessages;
  const isScreeningEnabled = statusState === 'ready' && enabled && available;
  const statusText = statusState === 'loading'
    ? t('screening.status.checking')
    : statusState === 'error'
      ? t('screening.status.unknown')
      : !enabled
        ? t('screening.status.disabled')
        : available ? t('screening.status.enabled') : t('screening.status.unavailable');

  const applyScreenResult = useCallback((result: ScreeningScreenResponse) => {
    const nextCandidates = result.candidates || [];
    setScreenMeta(result);
    setCandidates(nextCandidates);
    setExpandedCode(nextCandidates[0]?.code ?? null);
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const result = await screeningApi.getHistory({ limit: 10 });
      setHistoryRuns(result.runs || []);
    } catch (err) {
      setHistoryError(toApiErrorMessage(err, t('screening.error.historyLoad')));
    } finally {
      setHistoryLoading(false);
    }
  }, [t]);

  const handleHistoryRunSelect = useCallback(async (runId: string) => {
    // 竞态防护：快速切换历史条目时，只应用最新一次请求的响应
    const requestId = historyRunRequestIdRef.current + 1;
    historyRunRequestIdRef.current = requestId;
    const isCurrentRequest = () => historyRunRequestIdRef.current === requestId;
    // 与运行中的选股任务互斥：手动选择历史记录后，暂停/取消后台任务轮询，
    // 避免任务完成后把当前任务的候选结果回写到历史上下文中。
    setActiveTaskId(null);
    setHistoryError('');
    setLoading(true);
    try {
      const detail = await screeningApi.getRun(runId);
      if (!isCurrentRequest()) {
        return;
      }
      if (detail?.result) {
        applyScreenResult(detail.result);
        historyContextStrategyRef.current = true;
        // 同步持久化恢复指针：刷新后应恢复用户刚选中的历史 run，
        // 而不是停留在更早的 task/run。历史详情不携带 taskId，以 runId
        // 作为占位——正常路径刷新走 getRun(runId) 恢复、不会触发轮询；
        // 若该 run 恢复失败，占位轮询会命中不可恢复错误并清理过期指针。
        persistScreenTask({
          taskId: runId,
          runId,
          market: detail.market || market,
          strategy: detail.strategy || strategy,
          maxResults,
        });
        // 同步历史 run 的策略与市场上下文，确保结果区文案和后续深度分析
        // 使用该历史 run 对应的 strategy/market，而不是当前表单的选择
        if (detail.strategy) {
          setStrategy(detail.strategy);
        }
        if (detail.market) {
          setMarket(detail.market);
        }
        setError('');
        setTaskProgress(100);
        setTaskMessage('screening.task.loadedHistory');
      } else {
        setError(t('screening.error.historyNotFound'));
      }
    } catch (err) {
      if (!isCurrentRequest()) {
        return;
      }
      setError(toApiErrorMessage(err, t('screening.error.historyResult')));
    } finally {
      if (isCurrentRequest()) {
        setLoading(false);
      }
    }
  }, [applyScreenResult, market, maxResults, strategy, t]);

  const clearScreeningResults = () => {
    setCandidates([]);
    setScreenMeta(null);
    setExpandedCode(null);
  };

  const loadHotspotDetail = useCallback(async (
    topic: string,
    options: { refresh?: boolean; includeSearch?: boolean } = {},
  ) => {
    if (!topic) {
      return;
    }
    const cachedDetail = !options.refresh && !options.includeSearch
      ? hotspotDetailsByTopicRef.current[topic]
      : null;
    if (cachedDetail) {
      setHotspotDetail(cachedDetail);
      setHotspotDetailError('');
      setLoadingHotspotDetail(false);
      return;
    }
    const requestId = hotspotDetailRequestIdRef.current + 1;
    hotspotDetailRequestIdRef.current = requestId;
    const isCurrentRequest = () => hotspotDetailRequestIdRef.current === requestId;
    const canApplyRequest = () => isCurrentRequest() && selectedHotspotTopicRef.current === topic;
    setLoadingHotspotDetail(!options.includeSearch);
    setSearchingHotspotNews(Boolean(options.includeSearch));
    setHotspotDetail((currentDetail) => (currentDetail?.topic === topic ? currentDetail : null));
    setHotspotDetailError('');
    try {
      const detail = await screeningApi.getHotspotDetail({
        topic,
        provider: 'akshare',
        refresh: options.refresh ?? false,
        ...(options.includeSearch ? { includeSearch: true } : {}),
      });
      if (!canApplyRequest()) {
        return;
      }
      const cacheableDetail = options.includeSearch
        ? hotspotDetailsByTopicRef.current[topic] || stripHotspotSearchAugmentation(detail)
        : stripHotspotSearchAugmentation(detail);
      hotspotDetailsByTopicRef.current = {
        ...hotspotDetailsByTopicRef.current,
        [topic]: cacheableDetail,
      };
      setHotspotDetail(options.includeSearch ? detail : cacheableDetail);
      if (options.includeSearch && detail.newsSearchStatus === 'no_results') {
        setHotspotDetailError(t('screening.error.hotspotNewsEmpty'));
      } else if (options.includeSearch && detail.newsSearchStatus !== 'available') {
        setHotspotDetailError(t('screening.error.hotspotNews'));
      }
    } catch (err) {
      if (!canApplyRequest()) {
        return;
      }
      if (!options.includeSearch) {
        setHotspotDetail(null);
      }
      setHotspotDetailError(toApiErrorMessage(
        err,
        options.includeSearch ? t('screening.error.hotspotNews') : t('screening.error.hotspotDetail'),
      ));
    } finally {
      if (isCurrentRequest()) {
        setLoadingHotspotDetail(false);
        setSearchingHotspotNews(false);
      }
    }
  }, [t]);

  const loadStrategies = useCallback(async () => {
    setLoadingStrategies(true);
    try {
      setStrategyLoadError('');
      const result = await screeningApi.getStrategies();
      const loadedStrategies = result.strategies || [];
      setStrategies(loadedStrategies);
      // 历史 run 的自定义/下线策略不在当前列表属预期行为，不做“回退第一项”的归一化，
      // 否则迟到的策略列表会把刚恢复好的上下文改写回默认策略。
      if (loadedStrategies.length > 0 && !historyContextStrategyRef.current) {
        setStrategy((currentStrategy) =>
          loadedStrategies.some((item) => item.id === currentStrategy) ? currentStrategy : loadedStrategies[0].id,
        );
      }
    } catch (err) {
      setStrategies([]);
      setStrategyLoadError(err instanceof Error ? err.message : t('screening.error.strategyLoad'));
    } finally {
      setLoadingStrategies(false);
    }
  }, [t]);

  const loadHotspots = useCallback(async (refresh = false) => {
    setLoadingHotspots(true);
    setHotspotError('');
    try {
      const result = await screeningApi.getHotspots({ provider: 'akshare', top: 12, refresh });
      const nextHotspots = result.hotspots || [];
      const nextDetails = stripHotspotSearchAugmentationByTopic(result.details || {});
      hotspotDetailsByTopicRef.current = {
        ...hotspotDetailsByTopicRef.current,
        ...nextDetails,
      };
      const currentTopic = selectedHotspotTopicRef.current;
      const retainedTopic = Boolean(currentTopic && nextHotspots.some((item) => item.topic === currentTopic));
      const nextTopic = retainedTopic ? currentTopic : null;
      setHotspots(nextHotspots);
      setHotspotsUpdatedAt(result.cachedAt || (nextHotspots.length > 0 ? new Date().toISOString() : null));
      setSelectedHotspotTopic(nextTopic);
      selectedHotspotTopicRef.current = nextTopic;
      setHotspotDetailError('');
      if (nextTopic && nextDetails[nextTopic]) {
        setHotspotDetail(nextDetails[nextTopic]);
        setLoadingHotspotDetail(false);
      } else if (!retainedTopic) {
        setHotspotDetail(null);
      } else if (refresh && nextTopic) {
        // A refreshed list and a retained detail must describe the same source
        // snapshot. The list endpoint intentionally omits details by default,
        // so explicitly bypass the detail cache for the retained topic.
        await loadHotspotDetail(nextTopic, { refresh: true });
      }
      if (nextHotspots.length === 0) {
        setHotspotError(formatHotspotEmptyMessage(result, t));
      }
    } catch (err) {
      setHotspotError(toApiErrorMessage(err, t('screening.error.hotspotLoad')));
    } finally {
      setLoadingHotspots(false);
    }
  }, [loadHotspotDetail, t]);

  const handleHotspotSelect = useCallback((topic: string) => {
    selectedHotspotTopicRef.current = topic;
    setSelectedHotspotTopic(topic);
    const cachedDetail = hotspotDetailsByTopicRef.current[topic];
    if (cachedDetail) {
      setHotspotDetail(cachedDetail);
      setHotspotDetailError('');
      setLoadingHotspotDetail(false);
    } else {
      const preview = hotspots.find((item) => item.topic === topic);
      setHotspotDetail((currentDetail) => (
        currentDetail?.topic === topic ? currentDetail : preview ? buildHotspotPreviewDetail(preview, t) : null
      ));
    }
  }, [hotspots, t]);

  const toggleHotspotsExpanded = useCallback(() => {
    setHotspotsExpanded((expanded) => {
      const nextExpanded = !expanded;
      if (!nextExpanded) {
        selectedHotspotTopicRef.current = null;
        setSelectedHotspotTopic(null);
        setHotspotDetail(null);
        setHotspotDetailError('');
      }
      return nextExpanded;
    });
  }, []);

  const handleAnalyzeHotspotStock = useCallback((stock: ScreeningHotspotDetail['stocks'][number]) => {
    const stockCode = String(stock.code || '').trim();
    if (!stockCode) {
      return;
    }
    const stockName = String(stock.name || stockCode).trim();
    navigate('/', {
      state: {
        stockCode,
        stockName,
        autoAnalyze: true,
        selectionSource: 'screening_hotspot',
        skills: ['hot_theme'],
      },
    });
  }, [navigate]);

  const handleAnalyzeCandidate = useCallback((candidate: ScreeningCandidate) => {
    const stockCode = String(candidate.code || '').trim();
    if (!stockCode) {
      return;
    }
    const stockName = String(candidate.name || stockCode).trim();
    const analysisSkills = (selectedStrategy?.analysisSkills || []).filter(Boolean);
    navigate('/', {
      state: {
        stockCode,
        stockName,
        autoAnalyze: true,
        selectionSource: 'screening_result',
        ...(analysisSkills.length > 0 ? { skills: analysisSkills } : {}),
      },
    });
  }, [navigate, selectedStrategy]);

  useEffect(() => {
    selectedHotspotTopicRef.current = selectedHotspotTopic;
  }, [selectedHotspotTopic]);

  useEffect(() => {
    if (!selectedHotspotTopic) {
      return;
    }
    void loadHotspotDetail(selectedHotspotTopic);
  }, [loadHotspotDetail, selectedHotspotTopic]);

  useEffect(() => {
    let active = true;
    screeningApi
      .getStatus()
      .then((status) => {
        if (!active) {
          return;
        }
        setEnabled(status.enabled);
        setAvailable(status.available);
        setStatusState('ready');
        if (status.enabled && status.available) {
          void loadStrategies();
          void loadHotspots(false);
          void loadHistory();
        }
      })
      .catch((err) => {
        if (active) {
          setStatusError(toApiErrorMessage(err, t('screening.error.statusUnknown')));
          setStatusState('error');
        }
      });
    return () => {
      active = false;
    };
  }, [loadHistory, loadHotspots, loadStrategies, statusAttempt, t]);

  // 刷新后优先从 history API 按 run_id 恢复结果；恢复失败再回退到 task 轮询
  useEffect(() => {
    const runId = restoredTask?.runId;
    if (!runId) {
      setRestoreResolved(true);
      return undefined;
    }
    let active = true;
    setLoading(true);
    // 记录自动恢复的请求基准：若在自动恢复返回前用户手动点开了历史记录
    // （historyRunRequestIdRef 被 handleHistoryRunSelect 递增），则放弃本次自动恢复响应，
    // 避免较晚返回的自动恢复把页面切回旧 run，覆盖用户最新一次的历史选择。
    const restoreRequestBase = historyRunRequestIdRef.current;
    screeningApi
      .getRun(runId)
      .then((detail) => {
        if (!active || historyRunRequestIdRef.current !== restoreRequestBase) {
          return;
        }
        if (detail?.result) {
          applyScreenResult(detail.result);
          historyContextStrategyRef.current = true;
          // 同步恢复该历史 run 的策略与市场上下文，避免结果区展示和
          // 深度分析沿用当前表单策略（与 handleHistoryRunSelect 一致）
          if (detail.strategy) {
            setStrategy(detail.strategy);
          }
          if (detail.market) {
            setMarket(detail.market);
          }
          setError('');
          setTaskProgress(100);
          setTaskMessage('screening.task.restoredHistory');
          setActiveTaskId(null);
        }
      })
      .catch(() => {
        // 历史记录恢复失败（run 不存在或服务重启），回退到 task 轮询；
        // 若用户已手动选择了历史记录，则不再回退，保持用户的选择。
        if (active && historyRunRequestIdRef.current === restoreRequestBase) {
          setActiveTaskId(restoredTask?.taskId ?? null);
        }
      })
      .finally(() => {
        // 无论结果是否过期都要解除自动恢复门闩，否则新任务的轮询会被阻塞到旧请求超时。
        if (active) {
          setRestoreResolved(true);
        }
        // 过期的自动恢复不得触碰共享 loading：用户手动选择的历史详情请求可能仍在飞行，
        // 提前清掉会重新放开“运行选股”入口，随后迟到的历史响应会覆盖新任务状态。
        if (active && historyRunRequestIdRef.current === restoreRequestBase) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [applyScreenResult, restoredTask]);

  useEffect(() => {
    if (!activeTaskId || !restoreResolved) {
      return undefined;
    }

    const pollingTaskId = activeTaskId;
    const taskContext = activeTaskContextRef.current?.taskId === pollingTaskId
      ? activeTaskContextRef.current : null;
    let active = true;
    let timer: number | undefined;

    function finishTask() {
      setActiveTaskId(null);
      setLoading(false);
    }

    function applyTaskStatus(task: ScreeningScreenTaskStatus) {
      const nextProgress = Number(task.progress ?? 0);
      setTaskProgress(Number.isFinite(nextProgress) ? nextProgress : 0);
      setTaskMessage(task.message || '');

      if (task.status === 'completed') {
        if (task.result) {
          applyScreenResult(task.result);
          setError('');
          // 持久化 runId：刷新后优先从 history API 恢复结果，而非依赖内存 task
          const completedRunId = task.result.runId || taskContext?.runId;
          if (completedRunId && taskContext) {
            persistScreenTask({
              ...taskContext,
              runId: completedRunId,
            });
          }
        } else {
          setError(t('screening.task.completedNoResult'));
          setCandidates([]);
          setScreenMeta(null);
        }
        finishTask();
        return;
      }

      if (task.status === 'failed') {
        setCandidates([]);
        setScreenMeta(null);
        setExpandedCode(null);
        setError(formatScreenTaskFailure(task.error || task.message, t));
        clearPersistedScreenTask();
        finishTask();
        return;
      }

      if (isRunningScreenTask(task.status)) {
        setLoading(true);
        timer = window.setTimeout(pollTask, SCREEN_TASK_POLL_INTERVAL_MS);
        return;
      }

      setError(t('screening.task.unknownStatus', { status: task.status || 'unknown' }));
      clearPersistedScreenTask();
      finishTask();
    }

    async function pollTask() {
      try {
        const task = await screeningApi.getScreenTask(pollingTaskId);
        if (!active) {
          return;
        }
        applyTaskStatus(task);
      } catch (err) {
        if (!active) {
          return;
        }
        const parsedError = getParsedApiError(err);
        if (isUnrecoverableScreenTaskError(parsedError)) {
          setError(formatParsedApiError(parsedError) || t('screening.task.unrecoverable'));
          setCandidates([]);
          setScreenMeta(null);
          clearPersistedScreenTask();
          finishTask();
          return;
        }
        setError(formatRecoverableScreenTaskPollingError(parsedError, t));
        setLoading(true);
        timer = window.setTimeout(pollTask, SCREEN_TASK_POLL_INTERVAL_MS);
      }
    }

    void pollTask();

    return () => {
      active = false;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [activeTaskId, applyScreenResult, restoreResolved, t]);

  const handleEnable = async () => {
    setEnabling(true);
    setError('');
    try {
      await screeningApi.enable();
      setEnabled(true);
      setAvailable(true);
      await loadStrategies();
    } catch (err) {
      try {
        const status = await screeningApi.getStatus();
        setEnabled(status.enabled);
        setAvailable(status.available);
        setStatusState('ready');
      } catch (statusErr) {
        setStatusError(toApiErrorMessage(statusErr, t('screening.error.statusUnknown')));
        setStatusState('error');
      }
      setError(err instanceof Error ? err.message : t('screening.task.enableFailed'));
    } finally {
      setEnabling(false);
    }
  };

  const handleStrategyChange = (nextStrategy: string) => {
    if (nextStrategy !== strategy) {
      clearScreeningResults();
    }
    setStrategy(nextStrategy);
  };

  const handleMarketChange = (nextMarket: string) => {
    if (nextMarket !== market) {
      clearScreeningResults();
    }
    setMarket(nextMarket);
  };

  const handleMaxResultsChange = (nextMaxResults: number) => {
    if (nextMaxResults !== maxResults) {
      clearScreeningResults();
    }
    setMaxResults(nextMaxResults);
  };

  const handleSubmit = async () => {
    // 新任务提交即代表用户放弃当前历史/恢复上下文：
    // 递增请求代号作废飞行中的历史详情与自动恢复响应；
    // 解除自动恢复门闩，保证刚提交的任务轮询立即可启动。
    historyRunRequestIdRef.current += 1;
    setRestoreResolved(true);
    setLoading(true);
    setError('');
    setScreenMeta(null);
    setTaskProgress(0);
    setTaskMessage('screening.task.submitting');
    try {
      const task = await screeningApi.startScreen({ market, strategy, maxResults });
      const taskContext = {
        taskId: task.taskId,
        market,
        strategy,
        maxResults,
      };
      activeTaskContextRef.current = taskContext;
      persistScreenTask(taskContext);
      setActiveTaskId(task.taskId);
      setTaskProgress(0);
      setTaskMessage(task.message || 'screening.task.submitted');
    } catch (err) {
      setCandidates([]);
      setLoading(false);
      setError(toApiErrorMessage(err, t('screening.task.submitFailed')));
    }
  };

  return (
    <AppPage className="max-w-6xl space-y-6 pb-12 pt-6">
      <div className="research-page-heading flex flex-wrap items-end justify-between gap-4">
        <h1 className="research-display text-foreground">{t('screening.pageTitle')}</h1>

        <div className="inline-flex w-fit items-center gap-2 rounded-2xl border border-border/70 bg-card/80 px-4 py-2 text-sm shadow-soft-card">
          <span className={`h-2.5 w-2.5 rounded-full ${isScreeningEnabled ? 'bg-success' : 'bg-warning'}`} />
          <span className="font-medium text-secondary-text">{statusText}</span>
        </div>
      </div>

      {statusState === 'loading' ? (
        <InlineAlert variant="info" message={t('screening.statusLoading')} />
      ) : null}

      {statusState === 'error' ? (
        <InlineAlert
          variant="warning"
          title={t('screening.statusLoadFailed')}
          message={statusError}
          action={
            <Button size="sm" onClick={() => {
              setStatusState('loading');
              setStatusError('');
              setStatusAttempt((attempt) => attempt + 1);
            }}>
              {t('screening.retry')}
            </Button>
          }
        />
      ) : null}

      {statusState === 'ready' && !enabled ? (
        <InlineAlert
          variant="info"
          title={t('screening.enableTitle')}
          message={t('screening.enableMessage')}
          action={
            <Button size="sm" isLoading={enabling} loadingText={t('screening.enabling')} onClick={() => void handleEnable()}>
              {t('screening.enable')}
            </Button>
          }
        />
      ) : null}

      {statusState === 'ready' && enabled && !available ? (
        <InlineAlert
          variant="warning"
          title={t('screening.unavailableTitle')}
          message={t('screening.unavailableMessage')}
        />
      ) : null}

      {error ? <InlineAlert variant="danger" title={t('screening.callFailed')} message={error} /> : null}

      <section className="rounded-2xl border border-border/80 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-start gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-orange-500/10 text-orange-500 shadow-[0_10px_30px_rgba(249,115,22,0.16)]">
              <Flame className="h-5 w-5" />
            </span>
            <h2 className="text-lg font-bold tracking-normal text-foreground">{t('screening.hotspot.title')}</h2>
          </div>
          <div className="flex flex-col items-start gap-2 lg:items-end">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={!isScreeningEnabled}
                onClick={toggleHotspotsExpanded}
              >
                <Bookmark className="h-4 w-4" />
                {hotspotsExpanded
                  ? t('screening.hotspot.collapse')
                  : hotspots.length
                    ? t('screening.hotspot.expandCount', { count: hotspots.length })
                    : t('screening.hotspot.expand')}
                <ChevronDown className={`h-4 w-4 transition-transform ${hotspotsExpanded ? 'rotate-180' : ''}`} />
              </Button>
              {hotspotsExpanded ? (
              <Button
                size="sm"
                variant="secondary"
                isLoading={loadingHotspots}
                loadingText={t('screening.hotspot.refreshing')}
                disabled={!isScreeningEnabled || loadingHotspots}
                onClick={() => void loadHotspots(true)}
              >
                <RefreshCw className="h-4 w-4" />
                {t('screening.hotspot.refresh')}
              </Button>
              ) : null}
            </div>
            {hotspotsUpdatedAt ? (
              <p className="text-xs text-secondary-text">
                {t('screening.hotspot.updatedAt', {
                  time: formatHotspotUpdatedAt(hotspotsUpdatedAt, language, t),
                })}
              </p>
            ) : null}
          </div>
        </div>

        {hotspotsExpanded && hotspotError ? (
          <p className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
            {hotspotError}
          </p>
        ) : null}

        {!hotspotsExpanded ? null : hotspots.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-4 py-6 text-sm text-secondary-text">
            {t('screening.hotspot.empty')}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {hotspots.map((item, index) => {
              const selected = selectedHotspotTopic === item.topic;
              const strength = getHotspotStrength(item, index, t);
              const iconMeta = getHotspotIcon(item.name || item.topic);
              const Icon = iconMeta.icon;
              return (
              <button
                key={`${item.topic}-${item.rank ?? ''}`}
                className={`group relative min-h-[116px] overflow-hidden rounded-xl border px-3 py-3 text-left transition-all ${
                  selected
                    ? 'border-primary/50 bg-primary/8'
                    : 'border-border/80 bg-card hover:-translate-y-0.5 hover:border-orange-300/70 hover:shadow-soft-card'
                }`}
                type="button"
                onClick={() => handleHotspotSelect(item.topic)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <span
                      className={`grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-bold ${
                        index < 3 ? 'bg-orange-500 text-white shadow-[0_8px_24px_rgba(249,115,22,0.24)]' : 'bg-surface text-secondary-text'
                      }`}
                    >
                      {index + 1}
                    </span>
                    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${iconMeta.className}`}>
                      <Icon className="h-5 w-5" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-bold text-foreground">{item.name || item.topic}</p>
                      <span className={`mt-1 inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${strength.className}`}>
                        {strength.label}
                      </span>
                    </div>
                  </div>
                  <span className="shrink-0 text-2xl font-black leading-none text-orange-500">
                    {formatNumber(item.heatScore, 0)}
                  </span>
                </div>
                <div className="mt-4 grid max-w-[72%] gap-1 text-[11px] text-secondary-text">
                  <span>
                    {t('screening.hotspot.change')}{' '}
                    <strong className="font-semibold text-foreground">{formatHotspotMetric(item.changePct, t)}%</strong>
                  </span>
                  <span>
                    {t('screening.hotspot.trend')}{' '}
                    <strong className="font-semibold text-foreground">{formatHotspotMetric(item.trendScore, t)}</strong>
                    {' · '}
                    {t('screening.hotspot.persistence')}{' '}
                    <strong className="font-semibold text-foreground">{formatHotspotMetric(item.persistenceScore, t)}</strong>
                  </span>
                  <span>
                    {getHotspotSampleText(item, t)} · {t('screening.hotspot.leaders')} {getHotspotLeadersText(item, t)}
                  </span>
                </div>
                <div className="absolute bottom-3 right-3 opacity-95 transition-transform group-hover:scale-105">
                  <MiniSparkline score={item.heatScore} selected={selected} />
                </div>
              </button>
              );
            })}
          </div>
        )}

        {hotspotsExpanded && selectedHotspotTopic ? (
          <div className="mt-4 rounded-xl border border-border/80 bg-surface/80 p-4">
            <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-foreground">
                  {hotspotDetail?.name || selectedHotspotTopic}
                </h3>
                <p className="mt-1 text-xs leading-5 text-secondary-text">
                  {hotspotDetail
                    ? getHotspotSummaryText(hotspotDetail, selectedHotspot, t)
                    : loadingHotspotDetail
                      ? t('screening.hotspot.loadingDetail')
                      : t('screening.hotspot.clickDetail')}
                </p>
                {hotspotDetail?.canonicalTopic && hotspotDetail.canonicalTopic !== selectedHotspotTopic ? (
                  <p className="mt-1 text-[11px] text-secondary-text">
                    {t('screening.hotspot.canonicalTopic', { topic: hotspotDetail.canonicalTopic })}
                  </p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  isLoading={searchingHotspotNews}
                  loadingText={t('screening.hotspot.searching')}
                  disabled={loadingHotspotDetail || searchingHotspotNews}
                  onClick={() => void loadHotspotDetail(selectedHotspotTopic, { includeSearch: true })}
                >
                  <Search className="h-3.5 w-3.5" />
                  {t('screening.hotspot.searchNews')}
                </Button>
                {loadingHotspotDetail ? (
                  <span className="w-fit rounded-full bg-cyan/10 px-3 py-1 text-xs font-semibold text-cyan">
                    {t('screening.hotspot.loadingSupplement')}
                  </span>
                ) : null}
                {hotspotDetail?.qualityStatus ? (
                  <span className="w-fit rounded-full bg-warning/10 px-3 py-1 text-xs font-semibold text-warning">
                    {t('screening.hotspot.quality', { value: getHotspotQualityLabel(hotspotDetail.qualityStatus, t) })}
                  </span>
                ) : null}
                {hotspotDetail?.fallbackUsed || hotspotDetail?.stale ? (
                  <span className="w-fit rounded-full bg-warning/10 px-3 py-1 text-xs font-semibold text-warning">
                    {getHotspotFallbackLabel(hotspotDetail, t)}
                  </span>
                ) : null}
                {hotspotDetail?.stockCount != null ? (
                  <span className="w-fit rounded-full bg-orange-500/10 px-3 py-1 text-xs font-semibold text-orange-500">
                    {t('screening.hotspot.stockCount', { count: hotspotDetail.stockCount })}
                  </span>
                ) : null}
              </div>
            </div>

            {hotspotDetailError ? (
              <p className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {hotspotDetailError}
              </p>
            ) : null}

            {hotspotDetail && hasHotspotDetailDegradation(hotspotDetail) ? (
              <details className="mb-3 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                <summary className="cursor-pointer font-semibold">{t('screening.hotspot.degraded')}</summary>
                <div className="mt-2 space-y-1 leading-5">
                  {(hotspotDetail.missingFields || []).length > 0 ? (
                    <p>
                      {t('screening.hotspot.missing', {
                        fields: getHotspotMissingFieldLabels(hotspotDetail.missingFields, t)
                          .join(t('screening.listSeparator')),
                      })}
                    </p>
                  ) : null}
                  {getHotspotDiagnosticMessages(hotspotDetail.sourceErrors, t).map((message, index) => (
                    <p key={`${message}-${index}`}>{message}</p>
                  ))}
                </div>
              </details>
            ) : null}

            {hotspotDetail ? (
              <div className="grid gap-4 lg:grid-cols-[1fr_1.3fr]">
                <div>
                  <p className="mb-3 flex items-center gap-1.5 text-xs font-semibold text-secondary-text">
                    <Clock3 className="h-3.5 w-3.5 text-orange-500" />
                    {t('screening.hotspot.timeline')}
                  </p>
                  <div className="relative space-y-0 pl-4 before:absolute before:bottom-3 before:left-[5px] before:top-2 before:w-px before:bg-border">
                    {getHotspotRouteItems(hotspotDetail).map((item, index) => (
                      <div key={`${item.title}-${index}`} className="relative pb-4 last:pb-0">
                        <span className="absolute -left-4 top-1 h-2.5 w-2.5 rounded-full border border-orange-400 bg-card" />
                        <div className="rounded-lg border border-border/70 bg-card/80 p-3">
                          <p className="text-[11px] font-semibold text-orange-500">
                            {getRouteTimeLabel(item, language, t)}
                          </p>
                          <p className="mt-1 text-xs font-semibold text-foreground">
                            {formatHotspotRouteTitle(item.title, t)}
                          </p>
                          <p className="mt-1 text-xs leading-5 text-secondary-text">
                            {formatHotspotRouteDescription(item.description, t)}
                          </p>
                          {item.url ? (
                            <a
                              className="mt-2 inline-flex text-[11px] font-semibold text-cyan hover:text-foreground"
                              href={item.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {t('screening.hotspot.viewNews')}
                            </a>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-xs font-semibold text-secondary-text">{t('screening.hotspot.stocks')}</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {(hotspotDetail.stocks || []).slice(0, 10).map((stock) => (
                      <div key={`${stock.code || stock.name}`} className="rounded-lg border border-border/70 bg-card/80 p-3">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="truncate text-xs font-semibold text-foreground">{stock.name || stock.code || '-'}</p>
                            <p className="mt-1 text-[11px] text-secondary-text">{stock.code || '-'}</p>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <span className="rounded-full bg-cyan/10 px-2 py-1 text-[11px] font-semibold text-cyan">
                              {getHotspotRoleLabel(stock.role, t)}
                            </span>
                            {stock.code ? (
                              <button
                                type="button"
                                aria-label={t('screening.hotspot.analyzeAria', { name: stock.name || stock.code })}
                                className="inline-flex h-7 items-center gap-1 rounded-full border border-cyan/30 bg-cyan/10 px-2 text-[11px] font-semibold text-cyan transition-colors hover:border-cyan hover:bg-cyan/15 hover:text-foreground"
                                onClick={() => handleAnalyzeHotspotStock(stock)}
                              >
                                <Play className="h-3 w-3" />
                                {t('screening.hotspot.analyze')}
                              </button>
                            ) : null}
                          </div>
                        </div>
                        <p className="mt-2 text-[11px] text-secondary-text">
                          {t('screening.hotspot.stockMeta', {
                            change: formatStockChangeText(stock.changePct, t),
                            heat: formatNumber(stock.hotStockScore, 0),
                          })}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="rounded-2xl border border-cyan/35 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <SlidersHorizontal className="h-4 w-4 text-cyan" />
            {t('screening.form.section')}
          </div>
          <span className="rounded-full border border-cyan/30 bg-cyan/10 px-3 py-1 text-xs font-semibold text-cyan">
            {selectedStrategyTag}
          </span>
        </div>

        <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr_180px_auto] lg:items-end">
          <label className="space-y-2 text-xs font-medium text-secondary-text">
            {t('screening.form.market')}
            <select
              className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              value={market}
              disabled={loading}
              onChange={(event) => handleMarketChange(event.target.value)}
            >
              {MARKETS.map((item) => (
                <option key={item.id} value={item.id}>
                  {t('screening.market.cn')}
                </option>
              ))}
            </select>
          </label>

          <div className="space-y-2 text-xs font-medium text-secondary-text">
            <label htmlFor="screening-strategy">{t('screening.strategy.label')}</label>
            <Select
              id="screening-strategy"
              value={selectedStrategy ? strategy : CUSTOM_STRATEGY_OPTION_VALUE}
              disabled={loading || loadingStrategies}
              placeholder=""
              options={[
                ...strategies.map((item) => ({
                  value: item.id,
                  label: item.name || item.title || item.id,
                })),
                { value: CUSTOM_STRATEGY_OPTION_VALUE, label: t('screening.strategy.customOption') },
              ]}
              onChange={(value) =>
                handleStrategyChange(
                  value === CUSTOM_STRATEGY_OPTION_VALUE ? '' : value,
                )
              }
            />
          </div>

          {!selectedStrategy && !loadingStrategies ? (
            <label className="space-y-2 text-xs font-medium text-secondary-text lg:col-start-2">
              {t('screening.strategy.customId')}
              <input
                aria-label={t('screening.strategy.customId')}
                className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
                value={strategy}
                disabled={loading}
                placeholder={t('screening.strategy.customIdPlaceholder')}
                onChange={(event) => handleStrategyChange(event.target.value)}
              />
            </label>
          ) : null}

          <label className="space-y-2 text-xs font-medium text-secondary-text">
            {t('screening.form.maxResults')}
            <input
              className="h-11 w-full rounded-xl border border-border bg-surface px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
              type="number"
              min={1}
              max={100}
              value={maxResults}
              disabled={loading}
              onChange={(event) => handleMaxResultsChange(Number(event.target.value))}
            />
          </label>

          <Button
            className="h-11 min-w-40"
            isLoading={loading}
            loadingText={t('screening.form.screening')}
            disabled={!isScreeningEnabled || loading || !strategy.trim()}
            onClick={() => void handleSubmit()}
          >
            <Play className="h-4 w-4" />
            {t('screening.form.run')}
          </Button>
        </div>

        <div className="mt-3 rounded-xl border border-border/75 bg-surface/55 px-3 py-2 text-xs leading-5 text-secondary-text">
          {strategyLoadError
            ? strategyLoadError
            : selectedStrategy?.description || t('screening.strategy.fallbackDescription')}
        </div>
      </section>

      {loading || screenMeta ? (
        <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-3">
              <span className={`grid h-7 w-7 place-items-center rounded-full ${loading ? 'text-cyan' : 'text-success'}`}>
                {loading ? <CircleAlert className="h-5 w-5" /> : <CheckCircle2 className="h-5 w-5" />}
              </span>
              <div>
                <h2 className="text-sm font-semibold text-foreground">
                  {loading ? t('screening.result.running') : t('screening.result.completed')}
                </h2>
                <p className="mt-1 text-xs text-secondary-text">
                  {loading
                    ? `${resolvedTaskMessage || t('screening.result.executing')} · ${taskProgress}%`
                    : `${displayedStrategy} · ${t('screening.market.cn')}`}
                </p>
              </div>
            </div>
          </div>

          <details className="mt-3 border-t border-border/70 pt-3 text-xs text-secondary-text">
            <summary className="w-fit cursor-pointer font-medium text-secondary-text">
              {t('screening.result.details')}
            </summary>
            <div className="mt-2 grid gap-1">
              <span>{t('screening.result.task', { id: activeTaskId ? activeTaskId.slice(0, 12) : '-' })}</span>
              <span>{t('screening.result.runId', { id: screenMeta?.runId || '-' })}</span>
              <span>
                {t('screening.result.counts', {
                  snapshot: screenMeta?.snapshotCount ?? '-',
                  filtered: screenMeta?.afterFilterCount ?? '-',
                  candidates: screenMeta?.candidateCount ?? candidates.length,
                })}
              </span>
              <span>
                {t('screening.result.ranking', {
                  mode: screenMeta?.llmRanked
                    ? t('screening.result.ranking.llm')
                    : screenMeta
                      ? t('screening.result.ranking.factor')
                      : '-',
                })}
                {screenMeta?.llmModelUsed ? ` · ${screenMeta.llmModelUsed}` : ''}
                {screenMeta?.llmCoverage != null
                  ? ` · ${t('screening.result.coverage', { value: formatPercent(screenMeta.llmCoverage) })}`
                  : ''}
              </span>
              {screenMeta?.resultVariantPoolSize ? (
                <span>
                  {t('screening.result.variant', { pool: screenMeta.resultVariantPoolSize })}{' '}
                  {screenMeta.resultVariantApplied
                    ? t('screening.result.variantRotated', { count: screenMeta.resultVariantRotatedSlots ?? 0 })
                    : t('screening.result.variantBaseline')}
                </span>
              ) : null}
              <span>
                {t('screening.result.enrichment', {
                  enriched: screenMeta?.dsaEnrichment?.enrichedCount ?? '-',
                  requested: screenMeta?.dsaEnrichment?.requestedCount ?? '-',
                })}
              </span>
            </div>
          </details>
        </section>
      ) : null}

      {screenMeta && alertMessages.length > 0 ? (
        <InlineAlert
          variant={llmFailed ? 'warning' : 'info'}
          title={llmFailed ? t('screening.result.factorTitle') : t('screening.result.hint')}
          message={<ScreenAlertMessage messages={alertMessages} />}
        />
      ) : null}

      {screenMeta ? (
        <section className="rounded-2xl border border-border bg-card/95 p-4 shadow-soft-card">
          <div className="mb-5 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <h2 className="text-base font-semibold text-foreground">{t('screening.result.title')}</h2>
          <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-2 text-xs text-secondary-text">
            <Search className="h-4 w-4 text-cyan" />
            {t('screening.result.candidateCount', { count: candidates.length })}
          </div>
          </div>

        {candidates.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-surface/70 px-5 py-10 text-center">
            <p className="text-sm font-medium text-foreground">{t('screening.result.empty')}</p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border">
            <table className="w-full min-w-[860px] border-collapse text-sm">
              <thead className="bg-surface text-left text-xs text-secondary-text">
                <tr>
                  <th className="w-14 px-4 py-3 font-semibold">{t('screening.result.column.rank')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.code')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.name')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.industry')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.price')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.change')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.score')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.ranking')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.risk')}</th>
                  <th className="px-4 py-3 font-semibold">{t('screening.result.column.details')}</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((item) => {
                  const expanded = expandedCode === item.code;
                  const factors = getFactorEntries(item);
                  const selectionExplanations = getSelectionExplanations(item, t);
                  const selectionQuality = item.whySelected?.length
                    ? item.explanationQuality?.whySelected || 'unknown'
                    : 'unknown';
                  const llmInsightAvailable = hasLlmInsight(item);
                  const dsaWarnings = item.dsaContext?.warnings || [];
                  const dsaNews = item.dsaNews || [];
                  const dsaEvents = item.dsaEvents || [];
                  return (
                    <Fragment key={`${item.rank}-${item.code}`}>
                      <tr className="border-t border-border align-top transition-colors hover:bg-hover/50">
                        <td className="px-4 py-3 text-secondary-text">{item.rank}</td>
                        <td className="px-4 py-3 font-mono font-semibold text-foreground">{item.code}</td>
                        <td className="px-4 py-3 font-semibold text-foreground">{item.name || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{item.industry || '-'}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.price)}</td>
                        <td className="px-4 py-3 text-secondary-text">{formatNumber(item.changePct)}%</td>
                        <td className="px-4 py-3 font-bold text-cyan">{formatScore(item.score)}</td>
                        <td className="px-4 py-3 text-secondary-text">
                          {factorRanking ? t('screening.result.factorRanking') : formatScore(item.llmScore)}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`rounded-lg px-2.5 py-1 text-xs font-semibold ${getRiskClassName(item.riskLevel)}`}>
                            {getRiskLabel(item.riskLevel, t)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <button
                            className="text-sm font-semibold text-cyan transition-colors hover:text-foreground"
                            type="button"
                            onClick={() => setExpandedCode(expanded ? null : item.code)}
                          >
                            {expanded ? t('screening.result.collapse') : t('screening.result.expand')}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <tr className="border-t border-border bg-surface/45">
                          <td colSpan={10} className="px-4 py-4">
                            <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
                              <div className="space-y-3">
                                <div className="grid gap-3 md:grid-cols-2">
                                  <div className="rounded-xl border border-cyan/25 bg-cyan/5 px-3 py-2.5">
                                    <p className="text-xs font-semibold text-cyan">{t('screening.result.whySelected')}</p>
                                    <ExplanationItems
                                      items={selectionExplanations}
                                      emptyText={t('screening.result.whySelectedEmpty')}
                                    />
                                    {selectionExplanations.length > 0 ? (
                                      <p className="mt-2 text-xs text-secondary-text">
                                        {t('screening.result.quality', { value: selectionQuality })}
                                      </p>
                                    ) : null}
                                  </div>
                                  <div className="rounded-xl border border-orange-400/25 bg-orange-500/5 px-3 py-2.5">
                                    <p className="text-xs font-semibold text-orange-500">{t('screening.result.whyNow')}</p>
                                    <ExplanationItems
                                      items={item.whyNow}
                                      emptyText={t('screening.result.whyNowEmpty')}
                                    />
                                    {item.whyNow?.length ? (
                                      <p className="mt-2 text-xs text-secondary-text">
                                        {t('screening.result.quality', {
                                          value: item.explanationQuality?.whyNow || 'unknown',
                                        })}
                                      </p>
                                    ) : null}
                                  </div>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.summary')}</p>
                                  <p className="mt-1 text-sm leading-6 text-foreground">{getCandidateReason(item, t)}</p>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.signal')}</p>
                                  <p className="mt-1 text-sm text-foreground">{getSignal(item, t)}</p>
                                  <button
                                    className="mt-2 rounded-lg border border-cyan/40 px-3 py-1.5 text-xs font-semibold text-cyan transition-colors hover:bg-cyan/10"
                                    type="button"
                                    onClick={() => handleAnalyzeCandidate(item)}
                                  >
                                    {t('screening.result.deepAnalysis')}
                                  </button>
                                </div>
                                {item.dsaAnalysisSummary ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">{t('screening.result.enrichedSummary')}</p>
                                    <p className="mt-1 text-sm leading-6 text-foreground">
                                      {formatEnrichmentSummary(item.dsaAnalysisSummary, t)}
                                    </p>
                                  </div>
                                ) : null}
                                {llmInsightAvailable ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">{t('screening.result.llmInsight')}</p>
                                    <p className="mt-1 text-sm leading-6 text-foreground">{item.llmThesis || item.reason}</p>
                                    <p className="mt-1 text-xs text-secondary-text">
                                      {t('screening.result.llmMeta', {
                                        sector: item.llmSector || '-',
                                        theme: item.llmTheme || '-',
                                        confidence: formatPercent(item.llmConfidence),
                                      })}
                                    </p>
                                  </div>
                                ) : null}
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.riskTags')}</p>
                                  <p className="mt-1 text-sm text-foreground">
                                    {[...(item.riskFlags || []), ...(item.llmRisks || [])].length
                                      ? [...(item.riskFlags || []), ...(item.llmRisks || [])]
                                        .join(t('screening.clauseSeparator'))
                                      : t('screening.result.none')}
                                  </p>
                                </div>
                                {item.riskSummary ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">{t('screening.result.riskSummary')}</p>
                                    <p className="mt-1 text-sm text-foreground">{item.riskSummary}</p>
                                  </div>
                                ) : null}
                              </div>
                              <div className="space-y-3">
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.factors')}</p>
                                  <div className="mt-2 grid grid-cols-2 gap-2">
                                    {factors.length > 0 ? (
                                      factors.map(([key, value]) => (
                                        <div key={key} className="rounded-lg border border-border bg-card px-3 py-2">
                                          <span className="block text-xs text-secondary-text">
                                            {FACTOR_LABEL_KEYS[key] ? t(FACTOR_LABEL_KEYS[key]) : key}
                                          </span>
                                          <span className="text-sm font-semibold text-foreground">{formatNumber(value)}</span>
                                        </div>
                                      ))
                                    ) : (
                                      <span className="text-sm text-secondary-text">{t('screening.result.noFactors')}</span>
                                    )}
                                  </div>
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.amount')}</p>
                                  <p className="mt-1 text-sm text-foreground">{formatAmount(item.amount, language)}</p>
                                </div>
                                {item.llmWatchItems?.length ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">{t('screening.result.watchItems')}</p>
                                    <p className="mt-1 text-sm text-foreground">
                                      {item.llmWatchItems.join(t('screening.clauseSeparator'))}
                                    </p>
                                  </div>
                                ) : null}
                                {item.llmCatalysts?.length ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">{t('screening.result.catalysts')}</p>
                                    <p className="mt-1 text-sm text-foreground">
                                      {item.llmCatalysts.join(t('screening.clauseSeparator'))}
                                    </p>
                                  </div>
                                ) : null}
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.news')}</p>
                                  {dsaNews.length > 0 ? (
                                    <ul className="mt-1 space-y-1 text-sm text-foreground">
                                      {dsaNews.slice(0, 3).map((newsItem, newsIndex) => (
                                        <li key={`${item.code}-dsa-news-${newsIndex}`}>
                                          {newsItem.title || newsItem.snippet || '-'}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="mt-1 text-sm text-secondary-text">{t('screening.result.none')}</p>
                                  )}
                                </div>
                                <div>
                                  <p className="text-xs font-semibold text-secondary-text">{t('screening.result.events')}</p>
                                  {dsaEvents.length > 0 ? (
                                    <ul className="mt-1 space-y-1 text-sm text-foreground">
                                      {dsaEvents.slice(0, 3).map((eventItem, eventIndex) => (
                                        <li key={`${item.code}-dsa-event-${eventIndex}`}>
                                          {eventItem.title || eventItem.snippet || '-'}
                                        </li>
                                      ))}
                                    </ul>
                                  ) : (
                                    <p className="mt-1 text-sm text-secondary-text">{t('screening.result.none')}</p>
                                  )}
                                </div>
                                {dsaWarnings.length > 0 ? (
                                  <div>
                                    <p className="text-xs font-semibold text-secondary-text">{t('screening.result.warnings')}</p>
                                    <p className="mt-1 text-sm text-secondary-text">
                                      {dsaWarnings.join(t('screening.clauseSeparator'))}
                                    </p>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        </section>
      ) : null}

      <section className="rounded-2xl border border-border/80 bg-card/95 p-4 shadow-soft-card">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Clock3 className="h-4 w-4 text-cyan" />
            {t('screening.history.title')}
          </div>
          <button
            type="button"
            className="text-xs font-medium text-cyan transition-colors hover:text-foreground"
            onClick={() => void loadHistory()}
            disabled={historyLoading}
          >
            {historyLoading ? t('screening.history.loadingShort') : t('screening.history.refresh')}
          </button>
        </div>
        {historyError ? (
          <p className="mb-3 text-xs text-danger">{historyError}</p>
        ) : null}
        {historyRuns.length === 0 ? (
          <p className="py-3 text-center text-xs text-secondary-text">
            {historyLoading ? t('screening.history.loading') : t('screening.history.empty')}
          </p>
        ) : (
          <div className="divide-y divide-border/70">
            {historyRuns.map((run) => (
              <button
                key={run.runId}
                type="button"
                className="flex w-full items-center justify-between gap-3 py-2.5 text-left transition-colors hover:bg-hover/50"
                onClick={() => void handleHistoryRunSelect(run.runId)}
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-foreground">
                    {formatHistoryStrategyName(run.strategy, strategies, t)}
                    <span className="ml-2 text-xs font-normal text-secondary-text">
                      {formatHistoryMarketLabel(run.market, t)}
                    </span>
                  </span>
                  <span className="mt-0.5 block text-xs text-secondary-text">
                    {t('screening.history.returned', { count: run.candidateCount ?? 0 })}
                    {run.snapshotCount != null
                      ? t('screening.history.snapshot', { count: run.snapshotCount })
                      : ''}
                    {run.llmRanked ? t('screening.history.llmRanked') : ''}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-secondary-text">
                  {formatRunCreatedAt(run.createdAt, language, t)}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
    </AppPage>
  );
};

export default StockScreeningPage;
