import { type ReactNode, type RefObject } from 'react';
import {
  ArrowUp,
  ChevronDown,
  SlidersHorizontal,
  Square,
  Star,
} from 'lucide-react';
import { cn } from '../../utils/cn';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { InlineAlert } from '../common';
import type { SkillInfo } from '../../api/agent';

interface ChatInputAreaProps {
  t: TranslateFn;
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
  canToggleWatchlist: boolean;
  watchlistBusy: boolean;
  inWatchlist: boolean;
  onToggleWatchlist: () => void;
  watchlistMessage: string | null;
  objectStatusText: string;
}

/**
 * Composer area extracted from `ChatPage` (T40).
 *
 * Enter-to-send ignores IME composition, so confirming a Chinese/Japanese
 * candidate no longer submits a half-finished question.
 */
export default function ChatInputArea({
  t,
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
  canToggleWatchlist,
  watchlistBusy,
  inWatchlist,
  onToggleWatchlist,
  watchlistMessage,
  objectStatusText,
}: ChatInputAreaProps) {
  const selectedSkillIdSet = new Set(selectedSkillIds);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // IME composition guard (T40): Enter while composing confirms the candidate
    // instead of sending the message.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

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

        <div className="chat-composer">
          <textarea
            ref={composerRef}
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            rows={1}
            className="chat-composer-input"
            style={{ height: 'auto' }}
            onInput={(event) => {
              const target = event.target as HTMLTextAreaElement;
              target.style.height = 'auto';
              target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
            }}
          />
          <div className="chat-composer-bar">
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
        <div className="chat-composer-footer">
          <span className="chat-object-status" aria-label={t('chat.currentObjectLabel')}>
            {objectStatusText}
          </span>
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
