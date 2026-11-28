/**
 * Stock picker for the ask-stock composer.
 *
 * Desktop renders a popover above the trigger; phones reuse the shared
 * ``BottomSheet``. Selection always flows through ``onPick(item)`` — the parent
 * inserts ``formatStockMention`` into the composer and never auto-sends.
 */

import { useMemo, useRef, useState } from 'react';
import type React from 'react';
import { Search } from 'lucide-react';
import { searchStocks } from '../../utils/searchStocks';
import type { StockIndexItem, StockSuggestion } from '../../types/stockIndex';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { BottomSheet } from '../common';
import { useTouchLayout } from '../../hooks/useMediaQuery';
import { useId } from 'react';

const PICKER_LIMIT = 8;
const WATCHLIST_LIMIT = 6;

export interface ChatStockPickerProps {
  isOpen: boolean;
  onClose: () => void;
  onPick: (item: StockIndexItem) => void;
  index: StockIndexItem[];
  watchlistCodes: string[];
  t: TranslateFn;
  /** Optional trigger element to return focus to on close. */
  triggerRef?: React.RefObject<HTMLElement | null>;
}

export default function ChatStockPicker({
  isOpen,
  onClose,
  onPick,
  index,
  watchlistCodes,
  t,
  triggerRef,
}: ChatStockPickerProps) {
  const touchLayout = useTouchLayout();
  const listId = useId();
  const [manualQuery, setManualQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);

  const watchlistItems = useMemo(() => {
    const upper = new Set(watchlistCodes.map((code) => code.toUpperCase()));
    const matched: StockIndexItem[] = [];
    for (const item of index) {
      if (
        upper.has(item.displayCode.toUpperCase())
        || upper.has(item.canonicalCode.toUpperCase())
      ) {
        matched.push(item);
        if (matched.length >= WATCHLIST_LIMIT) break;
      }
    }
    return matched;
  }, [index, watchlistCodes]);

  const visible: StockSuggestion[] = useMemo(() => {
    if (!manualQuery.trim()) return [];
    return searchStocks(manualQuery, index, { limit: PICKER_LIMIT });
  }, [index, manualQuery]);

  const handlePick = (item: StockIndexItem) => {
    onPick(item);
    onClose();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      triggerRef?.current?.focus({ preventScroll: true });
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedIndex((h) => Math.min(h + 1, visible.length - 1));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((h) => Math.max(h - 1, 0));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const chosen = visible[highlightedIndex >= 0 ? highlightedIndex : 0];
      if (chosen) {
        const item = index.find((entry) => entry.canonicalCode === chosen.canonicalCode);
        if (item) handlePick(item);
      }
    }
  };

  const content = (
    <div className="chat-stock-picker" data-testid="chat-stock-picker">
      <div className="chat-stock-picker-search">
        <Search size={14} aria-hidden="true" />
        <input
          ref={inputRef}
          type="text"
          value={manualQuery}
          onChange={(event) => {
            setManualQuery(event.target.value);
            setHighlightedIndex(-1);
          }}
          onKeyDown={handleKeyDown}
          placeholder={t('chat.picker.searchPlaceholder')}
          aria-label={t('chat.picker.searchPlaceholder')}
          className="chat-stock-picker-input"
        />
      </div>

      {manualQuery.trim() ? (
        visible.length > 0 ? (
          <div className="chat-stock-picker-list" role="listbox" aria-label={t('chat.pickStock')}>
            {visible.map((suggestion, i) => {
              const item = index.find((entry) => entry.canonicalCode === suggestion.canonicalCode);
              if (!item) return null;
              return (
                <button
                  key={item.canonicalCode}
                  type="button"
                  role="option"
                  aria-selected={i === highlightedIndex}
                  className={`chat-stock-picker-row${i === highlightedIndex ? ' is-highlighted' : ''}`}
                  onMouseEnter={() => setHighlightedIndex(i)}
                  onClick={() => handlePick(item)}
                >
                  <span className="chat-stock-picker-market">{marketBadge(suggestion.market)}</span>
                  <span className="chat-stock-picker-name">{item.nameZh}</span>
                  <code className="chat-stock-picker-code">{item.displayCode}</code>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="chat-stock-picker-empty" role="status">
            {t('chat.picker.noResult', { query: manualQuery })}
          </div>
        )
      ) : watchlistItems.length > 0 ? (
        <>
          <div className="chat-stock-picker-section">{t('chat.picker.watchlist')}</div>
          <div className="chat-stock-picker-list" role="listbox" aria-label={t('chat.picker.watchlist')}>
            {watchlistItems.map((item) => (
              <button
                key={item.canonicalCode}
                type="button"
                className="chat-stock-picker-row"
                onClick={() => handlePick(item)}
              >
                <span className="chat-stock-picker-market">{marketBadge(item.market)}</span>
                <span className="chat-stock-picker-name">{item.nameZh}</span>
                <code className="chat-stock-picker-code">{item.displayCode}</code>
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );

  if (touchLayout) {
    return (
      <BottomSheet isOpen={isOpen} onClose={onClose} title={t('chat.pickStock')} testId="chat-stock-picker-sheet">
        {content}
      </BottomSheet>
    );
  }

  if (!isOpen) return null;

  return (
    <div className="chat-stock-picker-popover" id={listId}>
      {content}
    </div>
  );
}

function marketBadge(market: string): string {
  const labels: Record<string, string> = {
    CN: 'A股', HK: '港股', US: '美股', JP: '日股', KR: '韩股',
    INDEX: '指数', ETF: 'ETF', BSE: '北交所',
  };
  return labels[market] ?? market;
}
