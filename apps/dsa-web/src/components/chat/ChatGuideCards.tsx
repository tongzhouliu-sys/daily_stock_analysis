import { ArrowUpRight, Sparkles } from 'lucide-react';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';

/** Phones show four starter questions; the rest would need a scroll. */
const QUICK_QUESTIONS_MOBILE_LIMIT = 4;

interface ChatGuideCardsProps {
  t: TranslateFn;
  questions: Array<{ label: string }>;
  disabled: boolean;
  onSelect: (questionIndex: number) => void;
}

/**
 * Empty-state welcome card extracted from ChatPage (T40).
 */
export default function ChatGuideCards({ t, questions, disabled, onSelect }: ChatGuideCardsProps) {
  return (
    <section className="chat-empty" data-testid="chat-welcome" aria-labelledby="chat-welcome-title">
      <span className="chat-empty-icon" aria-hidden="true">
        <Sparkles size={20} />
      </span>
      <h3 id="chat-welcome-title" className="chat-empty-title">{t('chat.welcomeTitle')}</h3>
      <p className="chat-empty-description">{t('chat.emptyDescriptionDefault')}</p>
      <div className="chat-quick">
        <span className="chat-quick-label">{t('chat.quickQuestionLabel')}</span>
        <div className="chat-quick-grid">
          {questions.map((question, index) => (
            <button
              key={index}
              type="button"
              onClick={() => onSelect(index)}
              disabled={disabled}
              aria-label={question.label}
              className={cn('chat-quick-btn', index >= QUICK_QUESTIONS_MOBILE_LIMIT && 'is-extra')}
            >
              <span>{question.label}</span>
              <ArrowUpRight size={14} aria-hidden="true" />
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
