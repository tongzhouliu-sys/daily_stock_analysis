import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, RefreshCw, Sparkles, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import type {
  StockClarification,
  StockClarificationIdentity,
} from '../../stores/agentChatStore';

/**
 * The clarification is a short, local resolution pass: read the request,
 * match the mentioned object, compare it with the conversation anchor, then
 * expose one confirm action. Only after that pass does the 10s default reply
 * start, so opening a session never begins with a meaningless countdown.
 */
export const STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS = 10;
const ANALYZE_STEP_MS = 360;
const ANALYZE_SETTLE_MS = 260;

type ClarificationDecision = 'switch' | 'keep';
type Phase = 'analyzing' | 'ready' | 'resolved';

interface StockClarificationCardProps {
  messageId: string;
  clarification: StockClarification;
  autoConfirmEnabled: boolean;
  language?: 'zh' | 'en';
  onConfirm: (decision: ClarificationDecision, target: StockClarificationIdentity | null) => void;
  onRequestInput: () => void;
}

const identityLabel = (identity: StockClarificationIdentity): string =>
  `${identity.stock_name ? `${identity.stock_name} ` : ''}${identity.stock_code}`.trim();

const copyFor = (language: 'zh' | 'en') => {
  const zh = language !== 'en';
  return {
    aria: zh ? '分析标的确认' : 'Confirm analysis target',
    titleAnalyzing: zh ? '先确认要分析的对象' : 'Confirming the analysis target',
    titleSwitch: zh ? '已锁定新的分析标的' : 'New analysis target locked',
    titleKeep: zh ? '将继续分析当前标的' : 'Continuing with the current target',
    titleAsk: zh ? '需要你确认具体标的' : 'Choose the exact target',
    titleResolvedSwitch: zh ? '已确认切换' : 'Switch confirmed',
    titleResolvedKeep: zh ? '已确认保持当前标的' : 'Current target confirmed',
    subtitleAnalyzing: zh
      ? '正在理解你的提问，并核对股票或指数代码。'
      : 'Reading your request and matching a stock or index code.',
    subtitleSwitch: zh
      ? '确认后将按新标的继续分析，无需重新输入问题。'
      : 'Confirmation continues the analysis with the new target.',
    subtitleKeep: zh
      ? '未发现需要切换的新标的，将沿用当前讨论对象。'
      : 'No new target was found, so the current object stays active.',
    subtitleAsk: zh
      ? '存在多个可能标的，选择后即可继续分析。'
      : 'Several objects match; choose one to continue.',
    stepIntent: zh ? '理解提问意图' : 'Reading the request',
    stepUnique: (label: string) => zh ? `识别到 ${label}` : `Matched ${label}`,
    stepAmbiguous: (count: number) => zh
      ? `匹配到 ${count} 个候选标的，正在核对`
      : `Matched ${count} candidates; checking them`,
    stepMultiple: (count: number) => zh
      ? `问题中出现 ${count} 个标的，需要确认`
      : `The request mentions ${count} objects; confirmation needed`,
    stepConflict: (label: string) => zh
      ? `你提到 ${label}，与当前报告对象不一致`
      : `You mentioned ${label}, which differs from the report object`,
    stepMissing: zh ? '未发现明确的股票或指数代码' : 'No explicit stock or index code found',
    stepCurrent: (label: string) => zh ? `核对当前讨论对象：${label}` : `Current conversation object: ${label}`,
    stepNoCurrent: zh ? '当前没有正在讨论的标的' : 'No active conversation object',
    stepDecisionSwitch: (label: string) => zh ? `已锁定切换目标：${label}` : `Switch target locked: ${label}`,
    stepDecisionKeep: (label: string) => zh ? `将继续分析：${label}` : `Will continue with ${label}`,
    stepDecisionAsk: zh ? '等待你确认具体标的' : 'Waiting for you to choose a target',
    targetSwitch: zh ? '分析标的' : 'Target',
    targetKeep: zh ? '当前标的' : 'Current',
    currentTag: zh ? '当前' : 'Current',
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
    analyzingButton: zh ? '解析中…' : 'Resolving…',
    targetHint: zh
      ? 'Agent 会先理解意图，再自动确认，不需要你等待倒计时。'
      : 'The agent resolves intent first, then confirms automatically.',
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
  const [phase, setPhase] = useState<Phase>('analyzing');
  const [visibleSteps, setVisibleSteps] = useState(1);
  const [remaining, setRemaining] = useState(STOCK_CLARIFICATION_AUTO_CONFIRM_SECONDS);
  const [paused, setPaused] = useState(false);
  const resolvedRef = useRef(false);

  const { action, current, candidates, recommended } = clarification;
  const target = action === 'switch' ? recommended : current;

  const steps = useMemo(() => {
    const list: Array<{ key: string; text: string }> = [
      { key: 'intent', text: copy.stepIntent },
    ];
    if (clarification.reason === 'ambiguous_name') {
      list.push({ key: 'match', text: copy.stepAmbiguous(candidates.length) });
    } else if (clarification.reason === 'multiple_objects') {
      list.push({ key: 'match', text: copy.stepMultiple(candidates.length) });
    } else if (clarification.reason === 'hint_conflict') {
      list.push({
        key: 'match',
        text: copy.stepConflict(recommended ? identityLabel(recommended) : ''),
      });
    } else if (recommended) {
      list.push({ key: 'match', text: copy.stepUnique(identityLabel(recommended)) });
    } else {
      list.push({ key: 'match', text: copy.stepMissing });
    }
    list.push({
      key: 'context',
      text: current ? copy.stepCurrent(identityLabel(current)) : copy.stepNoCurrent,
    });
    if (action === 'switch' && recommended) {
      list.push({ key: 'decision', text: copy.stepDecisionSwitch(identityLabel(recommended)) });
    } else if (action === 'keep' && current) {
      list.push({ key: 'decision', text: copy.stepDecisionKeep(identityLabel(current)) });
    } else {
      list.push({ key: 'decision', text: copy.stepDecisionAsk });
    }
    return list;
  }, [action, candidates.length, clarification.reason, copy, current, recommended]);

  useEffect(() => {
    if (phase !== 'analyzing') return;
    if (visibleSteps >= steps.length) {
      const timer = window.setTimeout(() => setPhase('ready'), ANALYZE_SETTLE_MS);
      return () => window.clearTimeout(timer);
    }
    const timer = window.setTimeout(() => {
      setVisibleSteps((count) => count + 1);
    }, ANALYZE_STEP_MS);
    return () => window.clearTimeout(timer);
  }, [phase, steps.length, visibleSteps]);

  const trigger = useCallback((decision: ClarificationDecision) => {
    if (resolvedRef.current) return;
    resolvedRef.current = true;
    setPhase('resolved');
    onConfirm(decision, decision === 'switch' ? clarification.recommended : null);
  }, [clarification.recommended, onConfirm]);

  const chooseCandidate = useCallback((candidate: StockClarificationIdentity) => {
    if (resolvedRef.current) return;
    resolvedRef.current = true;
    setPhase('resolved');
    onConfirm('switch', candidate);
  }, [onConfirm]);

  useEffect(() => {
    if (phase !== 'ready' || !autoConfirmEnabled || paused || action === 'ask') return;
    const timer = window.setTimeout(() => {
      if (remaining <= 1) {
        trigger(action === 'keep' ? 'keep' : 'switch');
        return;
      }
      setRemaining((seconds) => seconds - 1);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [action, autoConfirmEnabled, paused, phase, remaining, trigger]);

  const handleRequestInput = useCallback(() => {
    setPaused(true);
    onRequestInput();
  }, [onRequestInput]);

  const title = phase === 'analyzing'
    ? copy.titleAnalyzing
    : phase === 'resolved'
      ? action === 'switch' ? copy.titleResolvedSwitch : action === 'keep' ? copy.titleResolvedKeep : copy.titleAsk
      : action === 'switch'
        ? copy.titleSwitch
        : action === 'keep'
          ? copy.titleKeep
          : copy.titleAsk;
  const subtitle = phase === 'analyzing'
    ? copy.subtitleAnalyzing
    : action === 'switch'
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
        phase === 'resolved' && 'is-resolved',
      )}
      data-message-id={messageId}
      role="group"
      aria-label={copy.aria}
    >
      <div className="chat-stock-clarify-head">
        <span
          className={cn(
            'chat-stock-clarify-orb',
            phase === 'analyzing' && 'is-working',
            phase === 'resolved' && 'is-done',
          )}
          aria-hidden="true"
        >
          {phase === 'resolved' ? <Check size={16} /> : <Sparkles size={16} />}
          {phase === 'analyzing' && <span className="chat-stock-clarify-orbit" />}
        </span>
        <div className="chat-stock-clarify-heading">
          <p className="chat-stock-clarify-title" aria-live="polite">{title}</p>
          <p className="chat-stock-clarify-subtitle">
            {phase === 'resolved'
              ? action === 'ask' ? copy.resolvedAsk : action === 'keep' ? copy.resolvedKeep : copy.resolvedSwitch
              : subtitle}
          </p>
        </div>
      </div>

      <ol className="chat-stock-clarify-steps" aria-live="polite">
        {steps.map((step, index) => {
          const visible = index < visibleSteps;
          const done = visible && (index < visibleSteps - 1 || phase !== 'analyzing');
          return (
            <li
              key={step.key}
              className={cn(
                'chat-stock-clarify-step',
                visible && 'is-visible',
                done && 'is-done',
              )}
            >
              <span className="chat-stock-clarify-step-dot" aria-hidden="true" />
              <span>{step.text}</span>
            </li>
          );
        })}
      </ol>

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
                  disabled={phase === 'resolved'}
                >
                  <span>{candidate.stock_name || candidate.stock_code}</span>
                  <code>{candidate.stock_code}</code>
                </button>
              ))}
          </div>
        </div>
      )}

      {phase === 'ready' && autoConfirmEnabled && !paused && action !== 'ask' && (
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
        {phase === 'analyzing' ? (
          <button type="button" className="chat-stock-clarify-btn is-primary is-busy" disabled>
            <span className="chat-stock-clarify-spinner" aria-hidden="true" />
            <span>{copy.analyzingButton}</span>
          </button>
        ) : phase === 'resolved' ? null : action === 'switch' ? (
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
