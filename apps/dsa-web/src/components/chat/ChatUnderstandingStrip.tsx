import { Sparkles } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { AnalysisBrief, ObjectIdentity, Question } from '../../api/agentIntake';

interface ChatUnderstandingStripProps {
  understanding: {
    brief: AnalysisBrief;
    resolution?: { identity?: ObjectIdentity | null } | null;
    questions: Question[];
  };
}

const ORDER: Array<{ field: keyof AnalysisBrief; dimension: string; labelKey: string }> = [
  { field: 'goal', dimension: 'goal', labelKey: 'chat.understanding.dim.goal' },
  { field: 'horizon', dimension: 'horizon', labelKey: 'chat.understanding.dim.horizon' },
  { field: 'position', dimension: 'position', labelKey: 'chat.understanding.dim.position' },
  { field: 'risk_focus', dimension: 'risk_focus', labelKey: 'chat.understanding.dim.riskFocus' },
  { field: 'reading_style', dimension: 'reading_style', labelKey: 'chat.understanding.dim.readingStyle' },
] as const;

/**
 * Read-only summary of what the server understood, shown above the running
 * turn when the intake started automatically (no confirmation click).
 */
export function ChatUnderstandingStrip({ understanding }: ChatUnderstandingStripProps) {
  const { t } = useUiLanguage();
  const { brief, questions } = understanding;
  const identity = understanding.resolution?.identity ?? brief.object ?? null;

  const labelFor = (dimension: string, optionId: string): string => {
    const question = questions.find((item) => item.id === dimension);
    return question?.options.find((option) => option.id === optionId)?.label ?? optionId;
  };

  const chips = ORDER
    .map((entry) => {
      const raw = brief[entry.field];
      if (typeof raw !== 'string' || !raw) return null;
      return { key: entry.dimension, label: t(entry.labelKey as never), value: labelFor(entry.dimension, raw) };
    })
    .filter((chip): chip is { key: string; label: string; value: string } => chip !== null);

  if (!identity && chips.length === 0) return null;

  return (
    <section
      className="chat-understanding-strip"
      data-testid="chat-understanding-strip"
      aria-label={t('chat.understanding.title')}
    >
      <Sparkles size={13} className="chat-understanding-icon" aria-hidden="true" />
      <span className="chat-understanding-strip-title">{t('chat.understanding.title')}</span>
      {identity ? (
        <span className="chat-understanding-object">
          {identity.stock_name || identity.stock_code}
          <code>{identity.stock_code}</code>
          <span>{identity.market_label}</span>
        </span>
      ) : null}
      {chips.map((chip) => (
        <span key={chip.key} className="chat-understanding-chip is-static">
          <span className="chat-understanding-chip-dim">{chip.label}</span>
          <span>{chip.value}</span>
        </span>
      ))}
    </section>
  );
}

export default ChatUnderstandingStrip;
