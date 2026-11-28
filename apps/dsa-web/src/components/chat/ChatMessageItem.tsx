import { memo, type MouseEvent } from 'react';
import { Check, Copy, Download, Pencil, Sparkles } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Badge } from '../common';
import { formatChatTimestamp } from '../../utils/chatTime';
import ChatAnswer from './ChatAnswer';
import ChatThinkingBlock from './ChatThinkingBlock';
import { PredictionMarketOverviewPanel } from '../report/PredictionMarketOverviewPanel';
import StockClarificationCard from './StockClarificationCard';
import type { Message, StockClarification, StockClarificationIdentity } from '../../stores/agentChatStore';

type ClarificationDecision = 'switch' | 'keep';

interface ChatMessageItemProps {
  message: Message;
  skillLabel: string;
  backendBadgeLabel: string | null;
  language: 'zh' | 'en';
  thinkingExpanded: boolean;
  expandedCategories: Set<string>;
  onToggleCategory: (messageId: string, category: string) => void;
  onCollapseThinking: (messageId: string) => void;
  onFollowUp: (question: string) => void;
  clarification: StockClarification | null;
  clarificationAutoConfirmEnabled: boolean;
  copied: boolean;
  copyLabel: string;
  exportLabel: string;
  editLabel?: string;
  highlighted?: boolean;
  onCopy: (messageId: string, content: string) => void;
  onExport: (message: Message) => void;
  onEdit?: (message: Message) => void;
  onOpenPicker: () => void;
  onClarificationDecision: (
    message: Message,
    decision: ClarificationDecision,
    target: StockClarificationIdentity | null,
  ) => void;
  onMessageContextMenu?: (event: MouseEvent<HTMLDivElement>, message: Message) => void;
}

/**
 * Single chat message (T40).
 *
 * Memoized so that typing in the composer, or any unrelated state change in the
 * chat page, does not re-render the whole message thread.
 */
function ChatMessageItem({
  message,
  skillLabel,
  backendBadgeLabel,
  language,
  thinkingExpanded,
  expandedCategories,
  onToggleCategory,
  onCollapseThinking,
  onFollowUp,
  clarification,
  clarificationAutoConfirmEnabled,
  copied,
  copyLabel,
  exportLabel,
  onCopy,
  onExport,
  editLabel,
  highlighted,
  onEdit,
  onOpenPicker,
  onClarificationDecision,
  onMessageContextMenu,
}: ChatMessageItemProps) {
  const isAssistant = message.role === 'assistant';
  const usedPredictionMarketTools = (message.thinkingSteps ?? []).some(
    (step) =>
      (step.type === 'tool_done' || step.type === 'tool_start') &&
      (step.tool === 'search_prediction_markets' || step.tool === 'get_prediction_market_overview'),
  );
  const timestamp = formatChatTimestamp(message.createdAt, language);

  return (
    <div
      className={cn('chat-msg', isAssistant ? 'chat-msg-ai' : 'chat-msg-user', highlighted && 'is-highlighted')}
      data-message-id={message.id}
      data-role={message.role}
      onContextMenu={onMessageContextMenu ? (event) => onMessageContextMenu(event, message) : undefined}
    >
      {isAssistant && (skillLabel || backendBadgeLabel) && (
        <div className="chat-msg-meta">
          {skillLabel ? (
            <Badge variant="info" className="chat-skill-badge shadow-none" aria-label={`技能 ${skillLabel}`}>
              <Sparkles size={12} aria-hidden="true" />
              {skillLabel}
            </Badge>
          ) : null}
          {backendBadgeLabel ? (
            <Badge variant={message.backend === 'codex_app_server' ? 'warning' : 'history'} size="sm">
              {backendBadgeLabel}
            </Badge>
          ) : null}
        </div>
      )}
      {isAssistant && (
        <ChatThinkingBlock
          messageId={message.id}
          steps={message.thinkingSteps ?? []}
          expanded={thinkingExpanded}
          expandedCategories={expandedCategories}
          onToggleCategory={onToggleCategory}
          onCollapse={onCollapseThinking}
          language={language}
        />
      )}
      {isAssistant ? (
        <>
          <div className="chat-prose">
            <ChatAnswer
              content={message.content}
              answerMeta={message.answerMeta}
              onFollowUp={onFollowUp}
            />
          </div>
          {usedPredictionMarketTools ? (
            <div className="mt-3">
              <PredictionMarketOverviewPanel language={language} />
            </div>
          ) : null}
          {clarification && (
            <StockClarificationCard
              messageId={message.id}
              clarification={clarification}
              autoConfirmEnabled={clarificationAutoConfirmEnabled}
              language={language}
              onConfirm={(decision, target) => onClarificationDecision(message, decision, target)}
              onOpenPicker={onOpenPicker}
            />
          )}
          <div className="chat-message-actions">
            <button
              type="button"
              onClick={() => onCopy(message.id, message.content)}
              className="chat-action-btn"
              aria-label={copyLabel}
            >
              {copied
                ? <Check size={14} aria-hidden="true" />
                : <Copy size={14} aria-hidden="true" />}
            </button>
            <button
              type="button"
              onClick={() => onExport(message)}
              className="chat-action-btn"
              aria-label={exportLabel}
            >
              <Download size={14} aria-hidden="true" />
            </button>
            {timestamp.full ? (
              <time
                className="chat-msg-time"
                dateTime={message.createdAt ?? undefined}
                title={timestamp.full}
              >
                {timestamp.short}
              </time>
            ) : null}
          </div>
        </>
      ) : (
        <>
          <div className="chat-user-bubble">{message.content}</div>
          <div className="chat-message-actions">
            {onEdit && editLabel ? (
              <button
                type="button"
                onClick={() => onEdit(message)}
                className="chat-action-btn"
                aria-label={editLabel}
              >
                <Pencil size={14} aria-hidden="true" />
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => onCopy(message.id, message.content)}
              className="chat-action-btn"
              aria-label={copyLabel}
            >
              {copied
                ? <Check size={14} aria-hidden="true" />
                : <Copy size={14} aria-hidden="true" />}
            </button>
            {timestamp.full ? (
              <time
                className="chat-msg-time"
                dateTime={message.createdAt ?? undefined}
                title={timestamp.full}
              >
                {timestamp.short}
              </time>
            ) : null}
          </div>
        </>
      )}
    </div>
  );
}

export default memo(ChatMessageItem);
