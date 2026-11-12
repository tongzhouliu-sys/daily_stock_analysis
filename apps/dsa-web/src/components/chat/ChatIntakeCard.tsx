import { useMemo, useState } from 'react';
import { cn } from '../../utils/cn';
import type {
  AnalysisBrief, Answer, IntakeStatus, Question, QuestionOption,
} from '../../api/agentIntake';
import { intakeOptionLabel, type IntakeLanguage } from '../../utils/intakeLabels';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import AnalysisBriefSummary from './AnalysisBriefSummary';

interface ChatIntakeCardProps {
  questions: Question[];
  answers?: Answer[];
  initialAnswers?: Answer[];
  revision?: number;
  status?: IntakeStatus;
  brief?: AnalysisBrief | null;
  originalMessage?: string;
  isLoading?: boolean;
  errorMessage?: string | null;
  onSave?: (answers: Answer[]) => void | Promise<boolean>;
  onConfirm: (answers: Answer[]) => void | Promise<boolean>;
  onSkip: () => void | Promise<boolean>;
  onCancel: () => void;
  onRetry?: () => void;
  onDirect?: () => void;
}

/** Read-only local preview built only from controlled option ids. */
function previewBrief(questions: Question[], answers: Answer[]): AnalysisBrief {
  const byId = new Map(answers.map((a) => [a.question_id, a]));
  const brief: AnalysisBrief = { brief_version: 1, focus_areas: [], unknown_fields: [] };
  for (const question of questions) {
    if (question.id === 'confirm') continue;
    const answer = byId.get(question.id);
    const unknown = !answer || answer.source === 'unknown' || !answer.option_ids.length
      || answer.option_ids.every((id) => question.options.find((o) => o.id === id)?.is_unknown);
    if (unknown) { brief.unknown_fields!.push(question.dimension); continue; }
    const value = answer!.option_ids[0];
    switch (question.dimension) {
      case 'object': brief.object_scope = value; break;
      case 'goal': brief.goal = value; break;
      case 'horizon': brief.horizon = value; break;
      case 'position': brief.position = value; break;
      case 'risk_focus': brief.risk_focus = value; break;
      case 'reading_style': brief.reading_style = value; break;
      case 'research_focus': brief.focus_areas = answer!.option_ids.slice(0, 2); break;
      default: break;
    }
  }
  return brief;
}

/** Four-step guided experience: question → choices → confirm → research. */
export default function ChatIntakeCard({
  questions, answers: controlledAnswers, initialAnswers, status, brief,
  originalMessage, isLoading = false, errorMessage, onSave, onConfirm,
  onSkip, onCancel, onRetry, onDirect,
}: ChatIntakeCardProps) {
  const { t, language } = useUiLanguage();
  const lang: IntakeLanguage = language === 'en' ? 'en' : 'zh';
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Answer[]>(initialAnswers ?? controlledAnswers ?? []);
  const [viewOverride, setViewOverride] = useState<'questions' | 'summary' | null>(null);

  const validIds = useMemo(() => new Set(questions.map((q) => q.id)), [questions]);
  const effectiveAnswers = useMemo(
    () => answers.filter((a) => validIds.has(a.question_id)),
    [answers, validIds],
  );
  const safeIndex = Math.min(index, Math.max(0, questions.length - 1));
  const view = viewOverride
    ?? (status === 'confirmed' || status === 'skipped' ? 'summary' : 'questions');
  const question = questions[safeIndex];
  const isLast = safeIndex === questions.length - 1;
  const progress = questions.length ? ((safeIndex + 1) / questions.length) * 100 : 0;
  const currentAnswer = useMemo(
    () => effectiveAnswers.find((a) => a.question_id === question?.id),
    [effectiveAnswers, question?.id],
  );

  const select = (option: QuestionOption) => {
    if (!question || isLoading) return;
    const previous = currentAnswer?.option_ids ?? [];
    let optionIds: string[];
    if (question.type === 'single') {
      optionIds = [option.id];
    } else if (previous.includes(option.id)) {
      optionIds = previous.filter((id) => id !== option.id);
    } else {
      const cap = question.max_selections ?? previous.length + 1;
      optionIds = previous.length >= cap ? [...previous.slice(1), option.id] : [...previous, option.id];
    }
    if (question.id === 'research_focus' && optionIds.includes('comprehensive') && optionIds.length > 1) {
      optionIds = optionIds.filter((id) => id === 'comprehensive');
    }
    const next: Answer = {
      question_id: question.id,
      option_ids: optionIds,
      source: option.is_unknown || optionIds.every((id) => question.options.find((o) => o.id === id)?.is_unknown)
        ? 'unknown' : 'user_choice',
    };
    setAnswers((prev) => [...prev.filter((a) => a.question_id !== question.id), next]);
  };

  const handleNext = async () => {
    if (!question || !currentAnswer?.option_ids.length || isLoading) return;
    if (!isLast) {
      if (question.id === 'goal' || question.id === 'confirm') {
        await onSave?.(effectiveAnswers);
      }
      setIndex((prev) => prev + 1);
      return;
    }
    if (onSave) {
      await onSave(effectiveAnswers);
      setViewOverride('summary');
      return;
    }
    await onConfirm(effectiveAnswers);
  };

  if (view === 'summary') {
    return (
      <AnalysisBriefSummary
        brief={brief ?? previewBrief(questions, effectiveAnswers)}
        originalMessage={originalMessage}
        skipped={status === 'skipped'}
        isLoading={isLoading}
        error={errorMessage}
        onStart={() => { void onConfirm(effectiveAnswers); }}
        onEdit={() => setViewOverride('questions')}
        onSkip={() => { void onSkip(); }}
        onCancel={onCancel}
      />
    );
  }

  if (!question) return null;

  return (
    <section className="chat-intake" aria-labelledby="chat-intake-heading">
      <header className="chat-intake-head">
        <h2 id="chat-intake-heading" className="chat-intake-title">{t('chat.intake.title')}</h2>
        <p className="chat-intake-subtitle">{t('chat.intake.subtitle')}</p>
      </header>

      {originalMessage && (
        <blockquote className="chat-intake-question-quote">
          <span className="chat-intake-quote-label">{t('chat.intake.yourQuestion')}</span>
          {originalMessage}
        </blockquote>
      )}

      <div className="chat-intake-progress" aria-live="polite">
        <div className="chat-intake-progress-text">
          <span>{t('chat.intake.progress')
            .replace('{current}', String(safeIndex + 1))
            .replace('{total}', String(questions.length))}</span>
          <span>{Math.round(progress)}%</span>
        </div>
        <div className="chat-intake-progress-track" aria-hidden="true">
          <div className="chat-intake-progress-fill" style={{ width: `${progress}%` }} />
        </div>
      </div>

      <fieldset className="chat-intake-question" disabled={isLoading} key={question.id}>
        <legend className="chat-intake-question-title">{question.title}</legend>
        {question.help && <p className="chat-intake-question-help">{question.help}</p>}
        <div className="chat-intake-options">
          {question.options.map((option) => {
            const selected = currentAnswer?.option_ids.includes(option.id) ?? false;
            return (
              <label key={option.id} className={cn('chat-intake-option', selected && 'is-selected')}>
                <input
                  type={question.type === 'multiple' ? 'checkbox' : 'radio'}
                  name={`intake-${question.id}`}
                  value={option.id}
                  checked={selected}
                  onChange={() => select(option)}
                />
                <span className="chat-intake-option-mark" aria-hidden="true" />
                <span className="chat-intake-option-label">
                  {intakeOptionLabel(option.id, lang, option.label)}
                </span>
                {selected && <span className="chat-intake-option-check" aria-hidden="true">✓</span>}
              </label>
            );
          })}
        </div>
      </fieldset>

      {errorMessage && (
        <div className="chat-intake-error" role="alert">
          <span>{errorMessage}</span>
          <div className="chat-intake-error-actions">
            {onRetry && <button type="button" onClick={onRetry}>{t('chat.intake.retry')}</button>}
            {onDirect && <button type="button" onClick={onDirect}>{t('chat.intake.direct')}</button>}
          </div>
        </div>
      )}

      <div className="chat-intake-actions">
        <div className="chat-intake-secondary-actions">
          <button
            type="button"
            onClick={() => setIndex((prev) => Math.max(0, prev - 1))}
            disabled={safeIndex === 0 || isLoading}
          >
            {t('chat.intake.previous')}
          </button>
          <button type="button" onClick={() => { void onSkip(); }} disabled={isLoading}>
            {t('chat.intake.generic')}
          </button>
        </div>
        <div className="chat-intake-secondary-actions">
          <button type="button" onClick={onCancel} disabled={isLoading}>
            {t('chat.intake.cancel')}
          </button>
          <button
            type="button"
            className="chat-intake-primary"
            onClick={() => { void handleNext(); }}
            disabled={!currentAnswer?.option_ids.length || isLoading}
          >
            {isLast ? t('chat.intake.confirmAnswers') : t('chat.intake.next')}
          </button>
        </div>
      </div>
    </section>
  );
}
