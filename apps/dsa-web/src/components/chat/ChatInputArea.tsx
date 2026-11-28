import { type ReactNode, type RefObject, useId, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  ChevronDown,
  Search,
  SlidersHorizontal,
  Square,
  Star,
} from 'lucide-react';
import { cn } from '../../utils/cn';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { InlineAlert } from '../common';
import type { SkillInfo } from '../../api/agent';
import { useTouchLayout } from '../../hooks/useMediaQuery';
import type { StockIndexItem } from '../../types/stockIndex';
import { searchStocks } from '../../utils/searchStocks';
import { MATCH_SCORE } from '../../utils/stockIndexFields';
import { deriveStockQuery, formatStockMention } from '../../utils/stockMention';
import ChatStockPicker from './ChatStockPicker';
import type { MentionLanguage } from '../../utils/stockMention';

const DEFAULT_COMPOSER_MAX_HEIGHT = 200;
const TOUCH_COMPOSER_MAX_HEIGHT = 128;

interface ChatInputAreaProps {
  t: TranslateFn;
  language: MentionLanguage;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  children?: ReactNode;
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  placeholder: string;
  disabled: boolean;
  loading: boolean;
  advancedOptionsOpen: boolean;
  onToggleAdvancedOptions: () => void;
  selectedSkillSummary: string;
  skills: SkillInfo[];
  selectedSkillIds: string[];
  skillLimitReached: boolean;
  showSkillDesc: string | null;
  onShowSkillDesc: (skillId: string | null) => void;
  onToggleSkill: (skillId: string) => void;
  onClearSkills: () => void;
  contextCompressionEnabled: boolean;
  contextCompressionLoaded: boolean;
  contextCompressionSaving: boolean;
  contextCompressionError: string | null;
  onToggleContextCompression: (enabled: boolean) => void;
  activeStockCode: string | null;
  activeStockName: string | null;
  stateContractLegacy: boolean;
  canToggleWatchlist: boolean;
  watchlistBusy: boolean;
  inWatchlist: boolean;
  onToggleWatchlist: () => void;
  watchlistMessage: string | null;
  stockIndex: StockIndexItem[];
  watchlistCodes: string[];
  /** Increment to open the stock picker from outside (e.g. the clarify card). */
  openPickerSignal?: number;
}

/**
 * Composer area extracted from `ChatPage` (T40).
 *
 * Enter-to-send ignores IME composition, so confirming a Chinese/Japanese
 * candidate no longer submits a half-finished question.
 *
 * The stock picker, passive autocomplete and "discussing" chip are all local
 * state here (T40's no-re-render-of-history constraint); they never touch the
 * store while typing.
 */
export default function ChatInputArea({
  t,
  language,
  composerRef,
  children,
  input,
  onInputChange,
  onSend,
  onStop,
  placeholder,
  disabled,
  loading,
  advancedOptionsOpen,
  onToggleAdvancedOptions,
  selectedSkillSummary,
  skills,
  selectedSkillIds,
  skillLimitReached,
  showSkillDesc,
  onShowSkillDesc,
  onToggleSkill,
  onClearSkills,
  contextCompressionEnabled,
  contextCompressionLoaded,
  contextCompressionSaving,
  contextCompressionError,
  onToggleContextCompression,
  activeStockCode,
  activeStockName,
  stateContractLegacy,
  canToggleWatchlist,
  watchlistBusy,
  inWatchlist,
  onToggleWatchlist,
  watchlistMessage,
  stockIndex = [],
  watchlistCodes = [],
  openPickerSignal = 0,
}: ChatInputAreaProps) {
  const selectedSkillIdSet = new Set(selectedSkillIds);
  const touchLayout = useTouchLayout();
  const composerMaxHeight = touchLayout ? TOUCH_COMPOSER_MAX_HEIGHT : DEFAULT_COMPOSER_MAX_HEIGHT;
  const listId = useId();

  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerTriggerRef = useRef<HTMLButtonElement>(null);
  // Passive autocomplete state (local; never lifted to ChatPage).
  const [autocompleteHighlight, setAutocompleteHighlight] = useState(-1);
  const [autocompleteDismissed, setAutocompleteDismissed] = useState<string | null>(null);
  const [autocompleteNavigated, setAutocompleteNavigated] = useState(false);

  const query = useMemo(() => deriveStockQuery(input), [input]);
  const suggestions = useMemo(() => {
    if (!query) return [];
    if (autocompleteDismissed === query) return [];
    return searchStocks(query, stockIndex, { limit: 5 });
  }, [query, stockIndex, autocompleteDismissed]);

  const showAutocomplete = useMemo(() => {
    if (!query || suggestions.length === 0 || touchLayout) return false;
    return suggestions[0].score >= MATCH_SCORE.PREFIX_MIN;
  }, [query, suggestions, touchLayout]);

  const touchSuggestions = touchLayout ? suggestions.slice(0, 3) : [];

  const insertMention = (item: StockIndexItem) => {
    const mention = formatStockMention(item, language);
    const cursor = composerRef.current?.selectionStart ?? input.length;
    const next = `${input.slice(0, cursor)}${mention} ${input.slice(cursor)}`;
    onInputChange(next);
    setAutocompleteDismissed(null);
    requestAnimationFrame(() => {
      const el = composerRef.current;
      if (el) {
        el.focus();
        const pos = cursor + mention.length + 1;
        el.setSelectionRange(pos, pos);
      }
    });
  };

  const handlePickStock = (item: StockIndexItem) => {
    insertMention(item);
  };

  const acceptAutocomplete = (index: number) => {
    const item = suggestions[index];
    if (!item) return;
    // Replace the trailing fragment with the mention, preserving the prefix.
    const queryStart = input.length - query.length;
    const prefix = input.slice(0, queryStart);
    const itemEntry = stockIndex.find((entry) => entry.canonicalCode === item.canonicalCode);
    if (!itemEntry) return;
    const mention = formatStockMention(itemEntry, language);
    onInputChange(`${prefix}${mention} `);
    setAutocompleteDismissed(null);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition guard (T40): Enter while composing confirms the candidate
    // instead of sending the message.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;

    if (showAutocomplete) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setAutocompleteNavigated(true);
        setAutocompleteHighlight((h) => (h + 1) % suggestions.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setAutocompleteNavigated(true);
        setAutocompleteHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setAutocompleteDismissed(query);
        return;
      }
      if (event.key === 'Tab') {
        if (suggestions[autocompleteHighlight >= 0 ? autocompleteHighlight : 0]) {
          event.preventDefault();
          acceptAutocomplete(autocompleteHighlight >= 0 ? autocompleteHighlight : 0);
          return;
        }
      }
      if (event.key === 'Enter' && autocompleteNavigated) {
        event.preventDefault();
        if (suggestions[autocompleteHighlight] && autocompleteHighlight >= 0) {
          acceptAutocomplete(autocompleteHighlight);
          return;
        }
      }
    }

    if (event.key !== 'Enter') return;
    // A phone keyboard has no Shift key, so Enter must insert a newline and the
    // send button becomes the only send path. Desktop keeps Enter-to-send.
    if (touchLayout) return;
    if (event.shiftKey) return;
    event.preventDefault();
    onSend();
  };

  // Reset the autocomplete highlight when the fragment changes, and open the
  // picker when an external signal (the clarify card) asks for it. Both use
  // React's "adjust state during render" pattern (store the previous value in
  // state, not a ref) so the React Compiler can reason about it.
  const [prevQuery, setPrevQuery] = useState(query);
  if (prevQuery !== query) {
    setPrevQuery(query);
    setAutocompleteHighlight(-1);
    setAutocompleteNavigated(false);
  }

  const [prevOpenSignal, setPrevOpenSignal] = useState(openPickerSignal);
  if (prevOpenSignal !== openPickerSignal) {
    setPrevOpenSignal(openPickerSignal);
    setPickerOpen(true);
  }

  const objectChip = useMemo(() => (
    activeStockCode
      ? { code: activeStockCode, name: activeStockName || activeStockCode }
      : null
  ), [activeStockCode, activeStockName]);

  const switchPreview = (() => {
    if (!objectChip) return null;
    // A mention still present in the draft that names a different stock.
    const mentionRe = /([㐀-鿿A-Za-z0-9]+)[（(]([A-Za-z0-9.]+)[)）]/g;
    for (const match of input.matchAll(mentionRe)) {
      const code = match[2].toUpperCase();
      const current = objectChip.code.toUpperCase();
      if (code && code !== current && !current.includes(code) && !code.includes(current)) {
        const entry = stockIndex.find((e) => e.displayCode.toUpperCase() === code || e.canonicalCode.toUpperCase() === code);
        if (entry) {
          return { name: entry.nameZh, code: entry.displayCode };
        }
      }
    }
    return null;
  })();

  return (
    <div className="chat-composer-area">
      <div className="chat-composer-stack">
        {children}

        {advancedOptionsOpen && (
          <div id="chat-advanced-options-panel" className="chat-advanced-panel">
            {skills.length > 0 && (
              <div
                id="chat-skill-picker-panel"
                data-testid="chat-skill-picker-panel"
                className="chat-skill-picker"
              >
                <span className="chat-advanced-label">策略</span>
                <label className="chat-skill-option">
                  <input
                    type="checkbox"
                    name="general-analysis"
                    value=""
                    checked={selectedSkillIds.length === 0}
                    onChange={onClearSkills}
                    className="chat-skill-checkbox"
                  />
                  <span className={cn(selectedSkillIds.length === 0 ? 'is-selected' : '')}>通用分析</span>
                </label>
                {skills.map((skill) => {
                  const checked = selectedSkillIdSet.has(skill.id);
                  const skillDisabled = !checked && skillLimitReached;
                  return (
                    <label
                      key={skill.id}
                      className={cn('chat-skill-option', skillDisabled && 'is-disabled')}
                      onMouseEnter={() => onShowSkillDesc(skill.id)}
                      onMouseLeave={() => onShowSkillDesc(null)}
                    >
                      <input
                        type="checkbox"
                        name="skills"
                        value={skill.id}
                        checked={checked}
                        disabled={skillDisabled}
                        onChange={() => onToggleSkill(skill.id)}
                        className="chat-skill-checkbox"
                      />
                      <span className={cn(checked ? 'is-selected' : '')}>{skill.name}</span>
                      {showSkillDesc === skill.id && skill.description && (
                        <div className="skill-desc-tooltip">
                          <p className="skill-title">{skill.name}</p>
                          <p>{skill.description}</p>
                        </div>
                      )}
                    </label>
                  );
                })}
              </div>
            )}
            <div className="chat-compression-row">
              <label
                className={cn(
                  'chat-skill-option',
                  contextCompressionLoaded && !contextCompressionSaving ? '' : 'is-disabled',
                )}
              >
                <input
                  type="checkbox"
                  checked={contextCompressionEnabled}
                  disabled={!contextCompressionLoaded || contextCompressionSaving}
                  onChange={(event) => onToggleContextCompression(event.target.checked)}
                  className="chat-skill-checkbox"
                />
                <span className="is-selected">上下文压缩</span>
                <span className="chat-advanced-hint">节省长会话 token</span>
              </label>
              <span className="chat-advanced-status">
                {contextCompressionSaving
                  ? '保存中...'
                  : contextCompressionEnabled
                    ? '已启用'
                    : '未启用'}
              </span>
            </div>
            {contextCompressionError ? (
              <InlineAlert
                variant="danger"
                title="上下文压缩设置未保存"
                message={contextCompressionError}
                className="chat-inline-notice"
              />
            ) : null}
          </div>
        )}

        {switchPreview && (
          <div className="chat-switch-preview" role="status" data-testid="chat-switch-preview">
            {t('chat.switchPreview', { name: `${switchPreview.name} ${switchPreview.code}` })}
          </div>
        )}

        {touchSuggestions.length > 0 && (
          <div className="chat-autocomplete-chips" role="listbox">
            {touchSuggestions.map((suggestion) => {
              const entry = stockIndex.find((e) => e.canonicalCode === suggestion.canonicalCode);
              if (!entry) return null;
              return (
                <button
                  key={entry.canonicalCode}
                  type="button"
                  className="chat-chip chat-autocomplete-chip"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => acceptAutocomplete(suggestions.indexOf(suggestion))}
                >
                  <span className="chat-chip-label">{entry.nameZh}</span>
                  <code>{entry.displayCode}</code>
                </button>
              );
            })}
          </div>
        )}

        <div className="chat-composer">
          <textarea
            ref={composerRef}
            data-testid="chat-composer-input"
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            rows={1}
            enterKeyHint={touchLayout ? 'enter' : 'send'}
            className="chat-composer-input"
            aria-autocomplete="list"
            aria-expanded={showAutocomplete}
            aria-controls={showAutocomplete ? listId : undefined}
            aria-activedescendant={
              showAutocomplete && autocompleteHighlight >= 0
                ? `${listId}-option-${autocompleteHighlight}`
                : undefined
            }
            style={{ height: 'auto' }}
            onInput={(event) => {
              const target = event.target as HTMLTextAreaElement;
              target.style.height = 'auto';
              target.style.height = `${Math.min(target.scrollHeight, composerMaxHeight)}px`;
            }}
          />
          <div className="chat-composer-bar">
            <button
              type="button"
              ref={pickerTriggerRef}
              className={cn('chat-chip', pickerOpen && 'is-active')}
              onClick={() => setPickerOpen((open) => !open)}
              aria-label={t('chat.pickStock')}
              aria-expanded={pickerOpen}
              aria-haspopup="dialog"
            >
              <Search size={13} aria-hidden="true" />
              {!touchLayout && <span className="chat-chip-label">{t('chat.pickStock')}</span>}
            </button>

            {objectChip ? (
              <button
                type="button"
                className="chat-chip chat-object-chip"
                aria-label={stateContractLegacy ? t('chat.objectUnconfirmedLegacy') : t('chat.currentObjectLabel')}
                onClick={() => setPickerOpen(true)}
              >
                <span className="chat-chip-label">
                  {t('chat.currentObject', { object: `${objectChip.name} · ${objectChip.code}` })}
                </span>
              </button>
            ) : null}

            <button
              type="button"
              className={cn('chat-chip', advancedOptionsOpen && 'is-active')}
              onClick={onToggleAdvancedOptions}
              aria-label={t('chat.advancedOptions')}
              aria-expanded={advancedOptionsOpen}
              aria-controls="chat-advanced-options-panel"
            >
              <SlidersHorizontal size={13} aria-hidden="true" />
              <span className="chat-chip-label">{selectedSkillSummary}</span>
              <ChevronDown
                className={cn('chat-chip-chevron', advancedOptionsOpen && 'is-open')}
                size={13}
                aria-hidden="true"
              />
            </button>

            {activeStockCode && canToggleWatchlist && (
              <button
                type="button"
                className="chat-chip"
                disabled={watchlistBusy}
                onClick={onToggleWatchlist}
              >
                <Star size={13} aria-hidden="true" />
                <span>{inWatchlist ? t('chat.watchlistRemove') : t('chat.watchlistAdd')}</span>
              </button>
            )}

            {watchlistMessage && (
              <span className="chat-watchlist-message">{watchlistMessage}</span>
            )}

            <span className="chat-composer-spacer" />

            {loading ? (
              <button
                type="button"
                className="chat-stop-btn"
                onClick={onStop}
                aria-label={t('chat.stop')}
              >
                <Square size={12} fill="currentColor" aria-hidden="true" />
                {t('chat.stop')}
              </button>
            ) : (
              <button
                type="button"
                className="chat-send-btn"
                onClick={onSend}
                disabled={!input.trim() || loading || disabled}
                aria-label="发送"
              >
                <ArrowUp size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>

        {/* Passive autocomplete popover (desktop only). */}
        {showAutocomplete && !touchLayout && (
          <div className="chat-autocomplete-popover" id={listId} role="listbox">
            {suggestions.map((suggestion, index) => {
              const entry = stockIndex.find((e) => e.canonicalCode === suggestion.canonicalCode);
              if (!entry) return null;
              return (
                <button
                  key={entry.canonicalCode}
                  type="button"
                  id={`${listId}-option-${index}`}
                  role="option"
                  aria-selected={index === autocompleteHighlight}
                  className={cn('chat-autocomplete-row', index === autocompleteHighlight && 'is-highlighted')}
                  onMouseEnter={() => {
                    setAutocompleteHighlight(index);
                    setAutocompleteNavigated(true);
                  }}
                  onClick={() => acceptAutocomplete(index)}
                >
                  <span className="chat-autocomplete-name">{entry.nameZh}</span>
                  <code className="chat-autocomplete-code">{entry.displayCode}</code>
                </button>
              );
            })}
          </div>
        )}

        <ChatStockPicker
          isOpen={pickerOpen}
          onClose={() => setPickerOpen(false)}
          onPick={handlePickStock}
          index={stockIndex}
          watchlistCodes={watchlistCodes}
          t={t}
          triggerRef={pickerTriggerRef}
        />

        <div className="chat-composer-footer">
          <span className="chat-disclaimer">
            {t('chat.generatedDisclaimer')}
            <span className="chat-disclaimer-separator" aria-hidden="true">·</span>
            {t('chat.composerHint')}
          </span>
        </div>
      </div>
    </div>
  );
}
