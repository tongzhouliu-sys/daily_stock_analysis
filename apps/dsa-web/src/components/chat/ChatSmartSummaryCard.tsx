import { useMemo, useState } from 'react';
import { Check, Sparkles, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { Question, Answer, IntakeStatus } from '../../api/agentIntake';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface ChatSmartSummaryCardProps {
  questions: Question[];
  answers: Answer[];
  status: IntakeStatus;
  originalMessage: string;
  isLoading?: boolean;
  onConfirm: (answers: Answer[]) => void | Promise<boolean>;
  onSkip: () => void | Promise<boolean>;
  onUpdateAnswer: (questionId: string, optionId: string) => void;
}

const LABELS: Record<string, Record<string, { zh: string; en: string }>> = {
  object: {
    a_share: { zh: 'A股', en: 'A-shares' },
    hk: { zh: '港股', en: 'HK' },
    us: { zh: '美股', en: 'US' },
    index: { zh: '指数', en: 'Index' },
  },
  goal: {
    worth_watching: { zh: '值得关注', en: 'Worth watching' },
    evaluate_holding: { zh: '评估持仓', en: 'Review holding' },
    understand_move: { zh: '了解涨跌', en: 'Why it moved' },
    learn_method: { zh: '学习方法', en: 'Learn method' },
  },
  horizon: {
    days: { zh: '数日', en: 'Days' },
    weeks: { zh: '1-4周', en: '1-4w' },
    months: { zh: '1-6月', en: '1-6m' },
    long: { zh: '6月+', en: '6m+' },
  },
  position: {
    not_held: { zh: '未持有', en: 'Not holding' },
    held_protect_profit: { zh: '持有·保护盈利', en: 'Holding·Protect' },
    held_control_drawdown: { zh: '持有·控制回撤', en: 'Holding·Limit DD' },
  },
  risk_focus: {
    downside: { zh: '控下行', en: 'Downside' },
    balanced: { zh: '均衡', en: 'Balanced' },
    tolerant: { zh: '高波动', en: 'Volatile' },
  },
  research_focus: {
    trend_levels: { zh: '趋势', en: 'Trend' },
    business_valuation: { zh: '估值', en: 'Valuation' },
    news_events: { zh: '新闻', en: 'News' },
    risks_counterpoints: { zh: '风险', en: 'Risks' },
    comprehensive: { zh: '综合', en: 'Full' },
  },
  reading_style: {
    highlights: { zh: '重点', en: 'Highlights' },
    detailed: { zh: '详细', en: 'Detailed' },
    plain: { zh: '通俗', en: 'Plain' },
  },
};

const DIM_LABELS: Record<string, { zh: string; en: string }> = {
  object: { zh: '市场', en: 'Market' },
  goal: { zh: '目标', en: 'Goal' },
  horizon: { zh: '周期', en: 'Horizon' },
  position: { zh: '持仓', en: 'Position' },
  risk_focus: { zh: '风险', en: 'Risk' },
  research_focus: { zh: '重点', en: 'Focus' },
  reading_style: { zh: '阅读', en: 'Style' },
};

function getLabel(dim: string, optId: string, isZh: boolean): string {
  const entry = LABELS[dim]?.[optId];
  if (!entry) return optId;
  return isZh ? entry.zh : entry.en;
}

function getDimLabel(dim: string, isZh: boolean): string {
  const entry = DIM_LABELS[dim];
  if (!entry) return dim;
  return isZh ? entry.zh : entry.en;
}

interface ResolvedItem {
  dim: string;
  optId: string;
  label: string;
}

export default function ChatSmartSummaryCard({
  questions, answers, originalMessage,
  isLoading = false, onConfirm, onSkip, onUpdateAnswer,
}: ChatSmartSummaryCardProps) {
  const { language } = useUiLanguage();
  const isZh = language !== 'en';
  const [expanded, setExpanded] = useState(false);

  const answerMap = useMemo(
    () => new Map(answers.map((a) => [a.question_id, a])),
    [answers],
  );

  // Split into resolved (answered or skipped) and pending
  const { resolved, pending } = useMemo(() => {
    const resolvedItems: ResolvedItem[] = [];
    const pendingQuestions: Question[] = [];
    for (const q of questions) {
      if (q.id === 'confirm') continue;
      const answer = answerMap.get(q.id);
      if (answer && answer.option_ids.length > 0) {
        const optId = answer.option_ids[0];
        const isUnknown = answer.source === 'unknown'
          || q.options.find((o) => o.id === optId)?.is_unknown;
        if (!isUnknown) {
          resolvedItems.push({ dim: q.dimension, optId, label: getLabel(q.id, optId, isZh) });
        }
      } else {
        pendingQuestions.push(q);
      }
    }
    return { resolved: resolvedItems, pending: pendingQuestions };
  }, [questions, answerMap, isZh]);

  const handleConfirm = () => { void onConfirm(answers); };
  const handleSkip = () => { void onSkip(); };

  const copy = {
    understood: isZh ? '已理解' : 'Understood',
    confirm: isZh ? '开始分析' : 'Start',
    skip: isZh ? '跳过' : 'Skip',
    adjust: isZh ? '调整' : 'Adjust',
    yourQuestion: isZh ? '你的问题' : 'Your question',
  };

  return (
    <section className="chat-smart-summary" aria-label={copy.understood}>
      {/* Compact header with original message */}
      <div className="chat-smart-summary-header">
        <Sparkles size={14} className="chat-smart-summary-sparkle" />
        <span className="chat-smart-summary-header-text">
          {copy.understood}
        </span>
        {originalMessage && (
          <span className="chat-smart-summary-msg">
            {originalMessage.length > 60
              ? `${originalMessage.slice(0, 60)}…`
              : originalMessage}
          </span>
        )}
      </div>

      {/* Resolved tags — always visible, inline */}
      {resolved.length > 0 && (
        <div className="chat-smart-summary-resolved">
          {resolved.map((item) => (
            <span key={item.dim} className="chat-smart-summary-tag">
              <span className="chat-smart-summary-tag-dim">
                {getDimLabel(item.dim, isZh)}
              </span>
              <span className="chat-smart-summary-tag-val">{item.label}</span>
            </span>
          ))}
        </div>
      )}

      {/* Pending dimensions — compact inline selectors */}
      {pending.length > 0 && (
        <div className="chat-smart-summary-pending">
          {!expanded ? (
            <button
              type="button"
              className="chat-smart-summary-toggle"
              onClick={() => setExpanded(true)}
              disabled={isLoading}
            >
              <span>{copy.adjust}</span>
              <ChevronDown size={14} />
              <span className="chat-smart-summary-pending-count">
                {pending.length}
              </span>
            </button>
          ) : (
            <div className="chat-smart-summary-pending-expanded">
              {pending.map((q) => (
                <div key={q.id} className="chat-smart-summary-dim-row">
                  <span className="chat-smart-summary-dim-label">
                    {getDimLabel(q.id, isZh)}
                  </span>
                  <div className="chat-smart-summary-dim-options">
                    {q.options
                      .filter((o) => !o.is_unknown)
                      .map((opt) => {
                        const selected = answerMap.get(q.id)?.option_ids.includes(opt.id);
                        return (
                          <button
                            key={opt.id}
                            type="button"
                            className={cn(
                              'chat-smart-summary-opt',
                              selected && 'is-selected',
                            )}
                            onClick={() => onUpdateAnswer(q.id, opt.id)}
                            disabled={isLoading}
                          >
                            {getLabel(q.id, opt.id, isZh)}
                          </button>
                        );
                      })}
                  </div>
                </div>
              ))}
              <button
                type="button"
                className="chat-smart-summary-collapse"
                onClick={() => setExpanded(false)}
                disabled={isLoading}
              >
                <ChevronUp size={14} />
              </button>
            </div>
          )}
        </div>
      )}

      {/* Action bar */}
      <div className="chat-smart-summary-actions">
        <button
          type="button"
          className="chat-smart-summary-confirm"
          onClick={handleConfirm}
          disabled={isLoading}
        >
          <Check size={14} />
          <span>{copy.confirm}</span>
        </button>
        <button
          type="button"
          className="chat-smart-summary-skip"
          onClick={handleSkip}
          disabled={isLoading}
        >
          {copy.skip}
        </button>
      </div>
    </section>
  );
}
