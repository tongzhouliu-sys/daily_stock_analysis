import type React from 'react';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { formatDateTime } from '../../utils/format';

export const isPresent = <T,>(value: T | null | undefined): value is T =>
  value !== undefined && value !== null && value !== '';

export const formatNumber = (value?: number, digits = 2): string =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '--';

export const formatChangePct = (value?: number): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
};

export const formatHistoryTime = (value?: string | null): string => {
  const formatted = formatDateTime(value);
  return formatted.length > 11 ? formatted.slice(5) : formatted;
};

export const getPriceChangeStyle = (value?: number): React.CSSProperties | undefined => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) {
    return undefined;
  }
  return { color: value > 0 ? 'var(--home-price-up)' : 'var(--home-price-down)' };
};

export const formatModelName = (value: string | undefined, t: TranslateFn): string => {
  const model = value?.trim();
  if (!model) {
    return t('stockTrend.neverRecorded');
  }
  const parts = model.split('/').filter(Boolean);
  return parts[parts.length - 1] || model;
};
