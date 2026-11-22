import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, RefreshCw, Sparkles, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import type {
  StockClarification,
  StockClarificationIdentity,
} from '../../stores/agentChatStore';

/**
 * Mid-conversation target confirmation.
 *
 * The server already decided what the turn is about, so the card renders that
 * conclusion immediately. It used to replay a fake four-step "resolving"
 * animation before revealing an answer it already had; that only added delay.
 * The 10s default reply stays: it is the only automatic action here.
 */
export const STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS = 10;

type ClarificationDecision = 'switch' | 'keep';

interface StockClarificationCardProps {
  messageId: string;
  clarification: StockClarification;
  autoConfirmEnabled: boolean;
  language?: 'zh' | 'en';
  onConfirm: (decision: ClarificationDecision, target: StockClarificationIdentity | null) => void;
  onRequestInput: () => void;
}

const copyFor = (language: 'zh' | 'en') => {
  const zh = language !== 'en';
  return {
    aria: zh ? '分析标的确认' : 'Confirm analysis target',
    titleSwitch: zh ? '已锁定新的分析标的' : 'New analysis target locked',
    titleKeep: zh ? '将继续分析当前标的' : 'Continuing with the current target',
    titleAsk: zh ? '需要你确认具体标的' : 'Choose the exact target',
    titleResolvedSwitch: zh ? '已确认切换' : 'Switch confirmed',
    titleResolvedKeep: zh ? '已确认保持当前标的' : 'Current target confirmed',
    subtitleSwitch: zh
      ? '确认后将按新标的继续分析，无需重新输入问题。'
      : 'Confirmation continues the analysis with the new target.',
    subtitleKeep: zh
      ? '未发现需要切换的新标的，将沿用当前讨论对象。'
      : 'No new target was found, so the current object stays active.',
    subtitleAsk: zh
      ? '存在多个可能标的，选择后即可继续分析。'
      : 'Several objects match; choose one to continue.',
    currentTag: zh ? '当前' : 'Current',
    targetSwitch: zh ? '分析标的' : 'Target',
    targetKeep: zh ? '当前标的' : 'Current',
    recommendedTag: zh ? '推荐' : 'Suggested',
    candidates: zh ? '其他候选' : 'Other candidates',
    switchButton: zh ? '切换并分析' : 'Switch & analyze',
    keepButton: zh ? '保持当前' : 'Keep current',
    continueButton: zh ? '继续分析' : 'Continue analysis',
    inputButton: zh ? '输入股票代码' : 'Enter a code',
    autoReply: (seconds: number) => zh
      ? `默认回复「确认」 · ${seconds}s 后自动发送`
      : `Default reply “Confirm” · auto-sends in ${seconds}s`,
    confirmNow: zh ? '立即确认' : 'Confirm now',
    resolvedSwitch: zh ? '已确认，正在切换分析标的…' : 'Confirmed. Switching the analysis target…',
    resolvedKeep: zh ? '已确认，正在继续分析…' : 'Confirmed. Continuing the analysis…',
    resolvedAsk: zh ? '请在上方输入股票或指数代码。' : 'Enter a stock or index code above.',
  };
};

const StockClarificationCard: React.FC<StockClarificationCardProps> = ({
  messageId,
  clarification,
  autoConfirmEnabled,
  language = 'zh',
  onConfirm,
  onRequestInput,
}) => {
  const copy = useMemo(() => copyFor(language), [language]);
  const [resolved, setResolved] = useState(false);
  const [remaining, setRemaining] = useState(STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS);
  const [paused, setPaused] = useState(false);
  const resolvedRef = useRef(false);

  const { action, current, candidates, recommended } = clarification;
  const target = action === 'switch' ? recommended : current;

  const trigger = useCallback((decision: ClarificationDecision) => {
    if (resolvedRef.current) return;
    resolvedRef.current = true;
    setResolved(true);
    onConfirm(decision, decision === 'switch' ? clarification.recommended : null);
  }, [clarification.recommended, onConfirm]);

  const chooseCandidate = useCallback((candidate: StockClarificationIdentity) => {
    if (resolvedRef.current) return;
    resolvedRef.current = true;
    setResolved(true);
    onConfirm('switch', candidate);
  }, [onConfirm]);

  useEffect(() => {
    if (resolved || !autoConfirmEnabled || paused || action === 'ask') return;
    const timer = window.setTimeout(() => {
      if (remaining <= 1) {
        trigger(action === 'keep' ? 'keep' : 'switch');
        return;
      }
      setRemaining((seconds) => seconds - 1);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [action, autoConfirmEnabled, paused, remaining, resolved, trigger]);

  const handleRequestInput = useCallback(() => {
    setPaused(true);
    onRequestInput();
  }, [onRequestInput]);

  const title = resolved
    ? action === 'switch'
      ? copy.titleResolvedSwitch
      : action === 'keep'
        ? copy.titleResolvedKeep
        : copy.titleAsk
    : action === 'switch'
      ? copy.titleSwitch
      : action === 'keep'
        ? copy.titleKeep
        : copy.titleAsk;
  const subtitle = action === 'switch'
    ? copy.subtitleSwitch
    : action === 'keep'
      ? copy.subtitleKeep
      : copy.subtitleAsk;
  const progress = Math.min(
    1,
    (STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS - remaining) / STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS,
  );
  const showAlternatives = candidates.length > 1
    && (action === 'ask' || Boolean(recommended))
    && candidates.some((candidate) => !recommended
      || candidate.canonical_id !== recommended.canonical_id
      || candidate.asset_type !== recommended.asset_type);

  return (
    <section
      className={cn(
        'chat-stock-clarify-card',
        'chat-understanding-card',
        resolved && 'is-resolved',
      )}
      data-message-id={messageId}
      role="group"
      aria-label={copy.aria}
    >
      <div className="chat-stock-clarify-head">
        <span
          className={cn('chat-stock-clarify-orb', resolved && 'is-done')}
          aria-hidden="true"
        >
          {resolved ? <Check size={16} /> : <Sparkles size={16} />}
        </span>
        <div className="chat-stock-clarify-heading">
          <p className="chat-stock-clarify-title" aria-live="polite">{title}</p>
          <p className="chat-stock-clarify-subtitle">
            {resolved
              ? action === 'ask' ? copy.resolvedAsk : action === 'keep' ? copy.resolvedKeep : copy.resolvedSwitch
              : subtitle}
          </p>
        </div>
      </div>

      {target ? (
        <div className="chat-stock-clarify-target-card">
          <span className="chat-stock-clarify-target-tag">
            {action === 'switch' ? copy.targetSwitch : copy.targetKeep}
          </span>
          <span className="chat-stock-clarify-target-name">
            {target.stock_name || target.stock_code}
          </span>
          <code className="chat-stock-clarify-target-code">{target.stock_code}</code>
          {action === 'switch' && <span className="chat-stock-clarify-target-recommended">{copy.recommendedTag}</span>}
        </div>
      ) : current ? (
        <div className="chat-stock-clarify-target-card">
          <span className="chat-stock-clarify-target-tag">{copy.currentTag}</span>
          <span className="chat-stock-clarify-target-name">
            {current.stock_name || current.stock_code}
          </span>
          <code className="chat-stock-clarify-target-code">{current.stock_code}</code>
        </div>
      ) : null}

      {showAlternatives && (
        <div className="chat-stock-clarify-candidates">
          <span className="chat-stock-clarify-candidates-label">{copy.candidates}</span>
          <div className="chat-stock-clarify-candidate-list">
            {candidates
              .filter((candidate) => !recommended
                || candidate.canonical_id !== recommended.canonical_id
                || candidate.asset_type !== recommended.asset_type)
              .map((candidate) => (
                <button
                  key={`${candidate.canonical_id}-${candidate.asset_type}`}
                  type="button"
                  className="chat-stock-clarify-candidate"
                  onClick={() => chooseCandidate(candidate)}
                  disabled={resolved}
                >
                  <span>{candidate.stock_name || candidate.stock_code}</span>
                  <code>{candidate.stock_code}</code>
                </button>
              ))}
          </div>
        </div>
      )}

      {!resolved && autoConfirmEnabled && !paused && action !== 'ask' && (
        <div className="chat-stock-clarify-timer">
          <div className="chat-stock-clarify-timer-row">
            <span className="chat-stock-clarify-timer-text">{copy.autoReply(remaining)}</span>
            <button
              type="button"
              className="chat-stock-clarify-now"
              onClick={() => trigger(action === 'keep' ? 'keep' : 'switch')}
            >
              {copy.confirmNow}
            </button>
          </div>
          <div className="chat-stock-clarify-progress" aria-hidden="true">
            <span style={{ width: `${progress * 100}%` }} />
          </div>
        </div>
      )}

      <div className="chat-stock-clarify-actions">
        {resolved ? null : action === 'switch' ? (
          <>
            <button
              type="button"
              className="chat-stock-clarify-btn is-primary"
              onClick={() => trigger('switch')}
            >
              <RefreshCw size={14} aria-hidden="true" />
              <span>{copy.switchButton}</span>
            </button>
            {current && (
              <button
                type="button"
                className="chat-stock-clarify-btn"
                onClick={() => trigger('keep')}
              >
                <X size={14} aria-hidden="true" />
                <span>{copy.keepButton}</span>
              </button>
            )}
            <button
              type="button"
              className="chat-stock-clarify-btn is-ghost"
              onClick={handleRequestInput}
            >
              <span>{copy.inputButton}</span>
            </button>
          </>
        ) : action === 'keep' ? (
          <>
            <button
              type="button"
              className="chat-stock-clarify-btn is-primary"
              onClick={() => trigger('keep')}
            >
              <Check size={14} aria-hidden="true" />
              <span>{copy.continueButton}</span>
            </button>
            <button
              type="button"
              className="chat-stock-clarify-btn is-ghost"
              onClick={handleRequestInput}
            >
              <span>{copy.inputButton}</span>
            </button>
          </>
        ) : (
          <>
            {current && (
              <button
                type="button"
                className="chat-stock-clarify-btn is-primary"
                onClick={() => trigger('keep')}
              >
                <Check size={14} aria-hidden="true" />
                <span>{copy.keepButton}</span>
              </button>
            )}
            <button
              type="button"
              className={cn('chat-stock-clarify-btn', !current && 'is-primary')}
              onClick={handleRequestInput}
            >
              <span>{copy.inputButton}</span>
            </button>
          </>
        )}
      </div>
    </section>
  );
};

export default StockClarificationCard;
