import { ArrowUpRight, Sparkles } from 'lucide-react';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import type { StockIndexItem } from '../../types/stockIndex';

/** Phones show four starter questions; the rest would need a scroll. */
const QUICK_QUESTIONS_MOBILE_LIMIT = 4;
const WATCHLIST_CHIP_LIMIT = 6;

interface ChatGuideCardsProps {
  t: TranslateFn;
  questions: Array<{ label: string }>;
  disabled: boolean;
  onSelect: (questionIndex: number) => void;
  /** Watchlist entries to offer as one-tap starters. */
  watchlistItems: StockIndexItem[];
  onPickWatchlist: (item: StockIndexItem) => void;
}

/**
 * Empty-state welcome card extracted from ChatPage (T40).
 */
export default function ChatGuideCards({
  t,
  questions,
  disabled,
  onSelect,
  watchlistItems,
  onPickWatchlist,
}: ChatGuideCardsProps) {
  return (
    <section className="chat-empty" data-testid="chat-welcome" aria-labelledby="chat-welcome-title">
      <span className="chat-empty-icon" aria-hidden="true">
        <Sparkles size={20} />
      </span>
      <h3 id="chat-welcome-title" className="chat-empty-title">{t('chat.welcomeTitle')}</h3>
      <p className="chat-empty-description">{t('chat.emptyDescriptionDefault')}</p>
      <p className="chat-input-formats" role="note">{t('chat.inputFormatsHint')}</p>

      {watchlistItems.length > 0 && (
        <div className="chat-guide-watchlist">
          <span className="chat-quick-label">{t('chat.guide.fromWatchlist')}</span>
          <div className="chat-guide-watchlist-row">
            {watchlistItems.slice(0, WATCHLIST_CHIP_LIMIT).map((item) => (
              <button
                key={item.canonicalCode}
                type="button"
                className="chat-guide-watchlist-chip"
                disabled={disabled}
                onClick={() => onPickWatchlist(item)}
              >
                <span>{item.nameZh}</span>
                <code>{item.displayCode}</code>
              </button>
            ))}
          </div>
        </div>
      )}

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
