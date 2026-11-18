import type { AnalysisReport } from '../types/analysis';
import { historyApi } from '../api/history';
import { looksLikeStockCode, validateStockCode } from './validation';
import { normalizeStockCode, resolveRegisteredIndexCanonical } from './stockCode';
import type { RegisteredIndexIdentity } from './stockCode';

/**
 * A follow-up hint is only a pre-accept draft. The server re-reads and
 * re-validates the report by primary key before it commits a user turn;
 * client-side history text is never sent as model material.
 */
export interface ChatFollowUpContext {
  stock_code: string;
  stock_name: string | null;
  report_record_id?: number | null;
}

export type ChatReportDraftErrorCode =
  | 'report_unavailable'
  | 'report_mismatch'
  | 'report_not_supported';

export class ChatFollowUpLoadError extends Error {
  readonly code: ChatReportDraftErrorCode;

  constructor(code: ChatReportDraftErrorCode) {
    super(code);
    this.name = 'ChatFollowUpLoadError';
    this.code = code;
  }
}

export interface ResolvedChatReport {
  recordId: number;
  stockCode: string;
  stockName: string | null;
  createdAt: string | null;
}

type ResolveChatReportDraftParams = {
  stockCode: string;
  recordId: number;
  index?: ReadonlyArray<RegisteredIndexIdentity>;
};

const MAX_FOLLOW_UP_NAME_LENGTH = 80;
const SUPPORTED_REPORT_TYPES = new Set(['simple', 'detailed', 'full', 'brief']);

function hasInvalidFollowUpNameCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  });
}

export function sanitizeFollowUpStockCode(stockCode: string | null): string | null {
  if (!stockCode) {
    return null;
  }

  const { valid, normalized } = validateStockCode(stockCode);
  return valid ? normalized : null;
}

export function sanitizeFollowUpStockName(stockName: string | null): string | null {
  const normalized = stockName?.trim().replace(/\s+/g, ' ') ?? '';
  if (!normalized) {
    return null;
  }

  if (
    normalized.length > MAX_FOLLOW_UP_NAME_LENGTH
    || hasInvalidFollowUpNameCharacter(normalized)
  ) {
    return null;
  }

  return normalized;
}

export function parseFollowUpRecordId(recordId: string | null): number | undefined {
  if (!recordId || !/^\d+$/.test(recordId)) {
    return undefined;
  }

  const parsed = Number(recordId);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    return undefined;
  }

  return parsed;
}

export function buildFollowUpPrompt(stockCode: string, stockName: string | null): string {
  const displayName = stockName ? `${stockName}(${stockCode})` : stockCode;
  return `请深入分析 ${displayName}`;
}

export function buildReportFollowUpContext(
  stockCode: string,
  stockName: string | null,
  recordId: number,
): ChatFollowUpContext {
  return { stock_code: stockCode, stock_name: stockName, report_record_id: recordId };
}

function reportIdentityKey(
  code: string,
  index: ReadonlyArray<RegisteredIndexIdentity>,
  type?: 'stock' | 'index',
): string | null {
  const trimmed = code.trim();
  if (!trimmed) return null;
  const registeredIndex = type !== 'stock' ? resolveRegisteredIndexCanonical(index, trimmed) : null;
  if (type === 'index' || registeredIndex) return `index:${registeredIndex ?? trimmed.toLowerCase()}`;
  const folded = trimmed.toUpperCase();
  const matches = new Set(index.filter((item) => item.assetType !== 'index'
    && reportCodeForms(item).includes(folded))
    .map((item) => item.canonicalCode.trim().toUpperCase()));
  if (matches.size > 1) return null;
  if (matches.size === 1) return `stock:${[...matches][0]}`;
  // Only HK format aliases can be folded without losing a market. Keep full
  // CN prefixes/suffixes and JP/KR suffixes when no registry proves equivalence.
  return `stock:${/^(?:HK\d{1,5}|\d{1,5}\.HK|\d{5})$/i.test(trimmed)
    ? normalizeStockCode(trimmed).toUpperCase() : folded}`;
}

function reportCodeForms(item: RegisteredIndexIdentity): string[] {
  const forms = [item.canonicalCode, item.displayCode, ...(item.aliases ?? [])]
    .filter(looksLikeStockCode).map((code) => code.trim().toUpperCase());
  // These equivalent spellings use the row's explicit market, never a market
  // inferred from bare digits. Name aliases are not code identity evidence.
  const canonical = item.canonicalCode.trim().toUpperCase();
  const cn = /^(\d{6})\.(SH|SZ|BJ)$/.exec(canonical);
  if (cn) forms.push(`${cn[2]}${cn[1]}`);
  const hk = /^(\d{1,5})\.HK$/.exec(canonical);
  if (hk) forms.push(normalizeStockCode(canonical));
  const us = /^([A-Z]{1,5})\.US$/.exec(canonical);
  if (us) forms.push(us[1]);
  return forms;
}

/**
 * Validate that a `recordId` from HomePage names a usable stock report before
 * the UI allows a user turn to carry it. All failures share one display path;
 * the server remains the only authority for the actual model input.
 */
export async function resolveChatReportDraft({
  stockCode,
  recordId,
  index = [],
}: ResolveChatReportDraftParams): Promise<ResolvedChatReport> {
  let report: AnalysisReport;
  try {
    report = await historyApi.getDetail(recordId);
  } catch {
    throw new ChatFollowUpLoadError('report_unavailable');
  }

  const meta = report?.meta;
  if (!meta) {
    throw new ChatFollowUpLoadError('report_unavailable');
  }
  const reportType = String(meta.reportType || '').toLowerCase();
  if (!SUPPORTED_REPORT_TYPES.has(reportType)) {
    throw new ChatFollowUpLoadError('report_not_supported');
  }
  if (!meta.stockCode || !meta.stockCode.trim()) {
    throw new ChatFollowUpLoadError('report_unavailable');
  }

  const requestKey = reportIdentityKey(stockCode, index);
  const reportKey = reportIdentityKey(meta.stockCode, index, meta.assetType);
  if (requestKey && reportKey && requestKey !== reportKey) {
    throw new ChatFollowUpLoadError('report_mismatch');
  }

  return {
    recordId,
    stockCode,
    stockName: sanitizeFollowUpStockName(meta.stockName ?? null),
    createdAt: meta.createdAt ?? null,
  };
}
