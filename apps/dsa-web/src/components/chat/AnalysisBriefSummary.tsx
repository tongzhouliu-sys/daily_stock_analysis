import type { AnalysisBrief } from '../../api/agentIntake';
import { BRIEF_DIMENSIONS, intakeOptionLabel, type IntakeLanguage } from '../../utils/intakeLabels';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface AnalysisBriefSummaryProps {
  brief: AnalysisBrief | null;
  originalMessage?: string;
  skipped?: boolean;
  isLoading?: boolean;
  error?: string | null;
  onStart: () => void;
  onEdit: () => void;
  onSkip: () => void;
  onCancel: () => void;
}

/** Confirmation step: one primary action, every background item editable. */
export default function AnalysisBriefSummary({
  brief, originalMessage, skipped = false, isLoading = false, error,
  onStart, onEdit, onSkip, onCancel,
}: AnalysisBriefSummaryProps) {
  const { t, language } = useUiLanguage();
  const lang: IntakeLanguage = language === 'en' ? 'en' : 'zh';
  const unknown = new Set(brief?.unknown_fields ?? []);

  const valueOf = (field: string): string | null => {
    if (!brief || skipped) return null;
    if (field === 'focus_areas') {
      const items = brief.focus_areas ?? [];
      return items.length
        ? items.map((id) => intakeOptionLabel(id, lang)).join(lang === 'en' ? ', ' : '、')
        : null;
    }
    const raw = (brief as unknown as Record<string, unknown>)[field];
    return typeof raw === 'string' && raw ? intakeOptionLabel(raw, lang, raw) : null;
  };

  return (
    <section className="chat-intake-review" aria-labelledby="chat-brief-heading">
      <header className="chat-intake-head">
        <h2 id="chat-brief-heading" className="chat-intake-title">{t('chat.intake.briefTitle')}</h2>
        <p className="chat-intake-subtitle">{t('chat.intake.subtitle')}</p>
      </header>

      {originalMessage && (
        <blockquote className="chat-intake-question-quote">
          <span className="chat-intake-quote-label">{t('chat.intake.yourQuestion')}</span>
          {originalMessage}
        </blockquote>
      )}

      <dl className="chat-intake-brief-list">
        {BRIEF_DIMENSIONS.map((dimension) => {
          const value = valueOf(dimension.field);
          const isUnknown = !value || unknown.has(dimension.field);
          const label = lang === 'en' ? dimension.en : dimension.zh;
          return (
            <div className="chat-intake-brief-row" key={dimension.field}>
              <dt>{label}</dt>
              <dd className={isUnknown ? 'is-unknown' : undefined}>
                {isUnknown ? t('chat.intake.notProvided') : value}
              </dd>
              <button
                type="button"
                className="chat-intake-brief-edit"
                onClick={onEdit}
                disabled={isLoading}
                aria-label={`${t('chat.intake.edit')} · ${label}`}
              >
                {t('chat.intake.edit')}
              </button>
            </div>
          );
        })}
      </dl>

      {error && <div className="chat-intake-error" role="alert">{error}</div>}

      <div className="chat-intake-actions">
        <div className="chat-intake-secondary-actions">
          <button type="button" onClick={onSkip} disabled={isLoading}>
            {t('chat.intake.generic')}
          </button>
          <button type="button" onClick={onCancel} disabled={isLoading}>
            {t('chat.intake.cancel')}
          </button>
        </div>
        <button type="button" className="chat-intake-primary" onClick={onStart} disabled={isLoading}>
          {t('chat.intake.start')}
        </button>
      </div>
    </section>
  );
}
