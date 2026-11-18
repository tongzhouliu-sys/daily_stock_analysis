import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, FileText, RefreshCw, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

/** Pre-accept draft states and post-accept session states share one source card. */
export type ReportFollowUpViewState = 'loading' | 'ready' | 'failed' | 'active' | 'unavailable';

export interface ReportFollowUpCardProps {
  state: ReportFollowUpViewState;
  recordId: number;
  stockCode: string | null;
  stockName: string | null;
  createdAt: string | null;
  error?: string | null;
  onRetry?: () => void;
  onRemove?: () => void;
}

const formatCreatedAt = (value: string | null, language: 'zh' | 'en'): string | null => {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
};

const ReportFollowUpCard: React.FC<ReportFollowUpCardProps> = ({
  state,
  recordId,
  stockCode,
  stockName,
  createdAt,
  error,
  onRetry,
  onRemove,
}) => {
  const { t, language } = useUiLanguage();
  const isBroken = state === 'failed' || state === 'unavailable';
  const isLoading = state === 'loading';
  const created = useMemo(() => formatCreatedAt(createdAt, language), [createdAt, language]);
  const label = stockName || stockCode || `#${recordId}`;

  const title = isLoading
    ? t('chat.reportLoadingTitle')
    : state === 'ready'
      ? t('chat.reportReadyTitle')
      : state === 'active'
        ? t('chat.reportActiveTitle')
        : state === 'unavailable'
          ? t('chat.reportUnavailableTitle')
          : t('chat.reportFailedTitle');

  return (
    <section
      className={cn(
        'chat-report-context',
        isLoading && 'is-loading',
        isBroken && 'is-broken',
        (state === 'ready' || state === 'active') && 'is-ready',
      )}
      data-testid="chat-report-context"
      data-state={state}
      role={isBroken ? 'alert' : 'status'}
      aria-live="polite"
      aria-label={t('chat.reportContextLabel')}
    >
      <span className="chat-report-context-icon" aria-hidden="true">
        {isBroken ? <AlertCircle size={15} /> : <FileText size={15} />}
        {isLoading && <span className="chat-report-context-spinner" />}
      </span>
      <div className="chat-report-context-body">
        <p className="chat-report-context-title">{title}</p>
        {isLoading ? (
          <p className="chat-report-context-subtitle">{t('chat.reportLoadingMessage')}</p>
        ) : isBroken ? (
          <>
            <p className="chat-report-context-subtitle">
              {error || (state === 'unavailable'
                ? t('chat.reportUnavailableMessage')
                : t('chat.reportFailedMessage'))}
            </p>
            <div className="chat-report-context-actions">
              {onRetry && (
                <button
                  type="button"
                  className="chat-report-context-action"
                  data-testid="chat-report-retry"
                  onClick={onRetry}
                >
                  <RefreshCw size={12} aria-hidden="true" />
                  {t('common.retry')}
                </button>
              )}
              {onRemove && (
                <button
                  type="button"
                  className="chat-report-context-action is-remove"
                  data-testid="chat-report-remove"
                  onClick={onRemove}
                >
                  <X size={12} aria-hidden="true" />
                  {t('chat.reportRemove')}
                </button>
              )}
            </div>
          </>
        ) : (
          <>
            <p className="chat-report-context-subtitle">
              {label}
              {stockCode && stockName ? ` · ${stockCode}` : null}
              {created ? ` · ${t('chat.reportSavedAt', { time: created })}` : null}
            </p>
            <div className="chat-report-context-actions">
              <Link
                className="chat-report-context-action is-link"
                data-testid="chat-report-view"
                to={`/?recordId=${recordId}`}
              >
                {t('chat.reportViewOriginal')}
              </Link>
              {onRemove && (
                <button
                  type="button"
                  className="chat-report-context-action is-remove"
                  data-testid="chat-report-remove"
                  onClick={onRemove}
                >
                  <X size={12} aria-hidden="true" />
                  {t('chat.reportRemove')}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
};

export default ReportFollowUpCard;
