import { useCallback, useMemo, useState } from 'react';
import { Check, Search, Sparkles, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { StockAutocomplete } from '../StockAutocomplete';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { Button } from '../common';
import type {
  Answer,
  IntakeDraft,
  ObjectIdentity,
  Question,
} from '../../api/agentIntake';

interface ChatUnderstandingCardProps {
  draft: IntakeDraft;
  isLoading?: boolean;
  /** Confirm the questionnaire; the caller sends and starts the analysis. */
  onConfirm: (answers: Answer[]) => void | Promise<unknown>;
  /** Return to the composer and retire the draft. */
  onCancel: () => void;
}

const DIM_LABEL_KEYS = {
  object: 'chat.understanding.dim.object',
  goal: 'chat.understanding.dim.goal',
  horizon: 'chat.understanding.dim.horizon',
  position: 'chat.understanding.dim.position',
  risk_focus: 'chat.understanding.dim.riskFocus',
  research_focus: 'chat.understanding.dim.researchFocus',
  reading_style: 'chat.understanding.dim.readingStyle',
} as const;

const dimLabelKey = (dimension: string): (typeof DIM_LABEL_KEYS)[keyof typeof DIM_LABEL_KEYS] =>
  DIM_LABEL_KEYS[dimension as keyof typeof DIM_LABEL_KEYS] ?? DIM_LABEL_KEYS.object;

const optionLabel = (question: Question, optionId: string): string =>
  question.options.find((option) => option.id === optionId)?.label ?? optionId;

/**
 * v2 first-question card.
 *
 * The server has already resolved the object and inferred the background. This
 * card shows what it understood; it only asks when the object is ambiguous or
 * missing. Preference questions are chips, never a questionnaire.
 */
export function ChatUnderstandingCard({
  draft,
  isLoading = false,
  onConfirm,
  onCancel,
}: ChatUnderstandingCardProps) {
  const { t } = useUiLanguage();
  const resolution = draft.resolution ?? null;
  const blocking = resolution?.status === 'ambiguous' || resolution?.status === 'missing';
  const candidates = useMemo(() => resolution?.candidates ?? [], [resolution]);

  const [answers, setAnswers] = useState<Answer[]>(() => [...(draft.answers ?? [])]);
  const [selectedObject, setSelectedObject] = useState<ObjectIdentity | null>(() => {
    if (resolution?.identity) return resolution.identity;
    const recommended = resolution?.recommended_option_id;
    return candidates.find((item) => item.canonical_id === recommended) ?? candidates[0] ?? null;
  });
  const [searchValue, setSearchValue] = useState('');

  const questionsById = useMemo(
    () => new Map(draft.questions.map((question) => [question.id, question])),
    [draft.questions],
  );
  const preferenceAnswers = useMemo(
    () => answers.filter((answer) => {
      if (answer.question_id === 'object') return false;
      return answer.option_ids.length > 0;
    }),
    [answers],
  );

  const removeAnswer = useCallback((questionId: string) => {
    setAnswers((previous) => previous.filter((answer) => answer.question_id !== questionId));
  }, []);

  const handleConfirm = useCallback(() => {
    const objectAnswer: Answer[] = selectedObject
      ? [{ question_id: 'object', option_ids: [selectedObject.canonical_id], source: 'user_choice' }]
      : [];
    void onConfirm([...preferenceAnswers.filter((answer) => answer.question_id !== 'object'), ...objectAnswer]);
  }, [onConfirm, preferenceAnswers, selectedObject]);

  const handleSearchSubmit = useCallback((code: string, name?: string) => {
    setSelectedObject({
      stock_code: code,
      stock_name: name ?? null,
      canonical_id: code,
      asset_type: 'stock',
      market: 'UNKNOWN',
      market_label: '',
    });
    setSearchValue('');
  }, []);

  if (!draft) return null;

  return (
    <section
      className="chat-understanding-card"
      data-testid="chat-understanding-card"
      data-status={resolution?.status ?? 'resolved'}
      aria-label={t('chat.understanding.title')}
    >
      <header className="chat-understanding-head">
        <Sparkles size={14} className="chat-understanding-icon" aria-hidden="true" />
        <span className="chat-understanding-title">
          {blocking ? (resolution?.status === 'missing'
            ? t('chat.understanding.missingTitle')
            : t('chat.understanding.askObject')) : t('chat.understanding.title')}
        </span>
        {resolution?.identity ? (
          <span className="chat-understanding-object" data-testid="chat-understanding-object">
            {resolution.identity.stock_name || resolution.identity.stock_code}
            <code>{resolution.identity.stock_code}</code>
            <span>{resolution.identity.market_label}</span>
          </span>
        ) : null}
      </header>

      {blocking && candidates.length > 0 ? (
        <div className="chat-understanding-candidates" role="radiogroup" aria-label={t('chat.understanding.askObject')}>
          {candidates.map((candidate) => {
            const checked = selectedObject?.canonical_id === candidate.canonical_id;
            return (
              <button
                key={candidate.canonical_id}
                type="button"
                role="radio"
                aria-checked={checked}
                className={cn('chat-understanding-candidate', checked && 'is-selected')}
                onClick={() => setSelectedObject(candidate)}
                disabled={isLoading}
              >
                <span className="chat-understanding-radio" aria-hidden="true">
                  {checked ? <Check size={12} /> : null}
                </span>
                <span className="chat-understanding-candidate-name">
                  {candidate.stock_name || candidate.stock_code}
                </span>
                <code>{candidate.stock_code}</code>
                <span className="chat-understanding-candidate-market">{candidate.market_label}</span>
                {resolution?.recommended_option_id === candidate.canonical_id ? (
                  <span className="chat-understanding-recommended">
                    {t('chat.understanding.recommended')}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}

      {blocking ? (
        <div className="chat-understanding-search">
          <label className="chat-understanding-search-label">
            <Search size={13} aria-hidden="true" />
            <span>{t('chat.understanding.searchPlaceholder')}</span>
          </label>
          <StockAutocomplete
            value={searchValue}
            onChange={setSearchValue}
            onSubmit={(code, name) => handleSearchSubmit(code, name)}
            placeholder={t('chat.understanding.searchPlaceholder')}
            disabled={isLoading}
          />
        </div>
      ) : null}

      {preferenceAnswers.length > 0 ? (
        <div className="chat-understanding-chips" aria-label={t('chat.understanding.fromQuestion')}>
          <span className="chat-understanding-chips-label">{t('chat.understanding.fromQuestion')}</span>
          {preferenceAnswers.map((answer) => {
            const question = questionsById.get(answer.question_id);
            if (!question) return null;
            const evidence = draft.brief_preview?.evidence?.[answer.question_id];
            return (
              <span
                key={answer.question_id}
                className="chat-understanding-chip"
                aria-label={evidence
                  ? `${t('chat.understanding.inferredTag')}: ${evidence}`
                  : undefined}
              >
                <span className="chat-understanding-chip-dim">
                  {t(dimLabelKey(question.dimension))}
                </span>
                <span>{optionLabel(question, answer.option_ids[0])}</span>
                <button
                  type="button"
                  className="chat-understanding-chip-remove"
                  aria-label={`${t('chat.understanding.remove')} ${optionLabel(question, answer.option_ids[0])}`}
                  onClick={() => removeAnswer(answer.question_id)}
                  disabled={isLoading}
                >
                  <X size={11} aria-hidden="true" />
                </button>
              </span>
            );
          })}
        </div>
      ) : null}

      {!blocking && !resolution?.identity && resolution?.status === 'resolved' ? (
        <p className="chat-understanding-note">{t('chat.understanding.autoStarted')}</p>
      ) : null}

      <div className="chat-understanding-actions">
        {draft.status === 'confirmed' && !blocking ? (
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => void onConfirm(preferenceAnswers)}
            disabled={isLoading}
            data-testid="chat-understanding-start"
          >
            {t('chat.understanding.continue')}
          </Button>
        ) : null}
        {blocking ? (
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={handleConfirm}
            disabled={isLoading || !selectedObject}
            data-testid="chat-understanding-confirm"
          >
            {selectedObject
              ? t('chat.understanding.start', { name: selectedObject.stock_name || selectedObject.stock_code })
              : t('chat.understanding.pickOne')}
          </Button>
        ) : null}
        <button
          type="button"
          className="chat-understanding-cancel"
          data-testid="chat-intake-cancel"
          onClick={onCancel}
          disabled={isLoading}
        >
          {t('chat.understanding.editQuestion')}
        </button>
      </div>
    </section>
  );
}

export default ChatUnderstandingCard;
