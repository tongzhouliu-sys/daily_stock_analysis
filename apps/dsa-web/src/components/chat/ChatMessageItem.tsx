import { memo, type MouseEvent } from 'react';
import { Check, Copy, Download, Sparkles } from 'lucide-react';
import { cn } from '../../utils/cn';
import { Badge } from '../common';
import ChatAnswer from './ChatAnswer';
import ChatThinkingBlock from './ChatThinkingBlock';
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
  onCopy: (messageId: string, content: string) => void;
  onExport: (message: Message) => void;
  onRequestComposerFocus: () => void;
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
  onRequestComposerFocus,
  onClarificationDecision,
  onMessageContextMenu,
}: ChatMessageItemProps) {
  const isAssistant = message.role === 'assistant';

  return (
    <div
      className={cn('chat-msg', isAssistant ? 'chat-msg-ai' : 'chat-msg-user')}
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
          {clarification && (
            <StockClarificationCard
              messageId={message.id}
              clarification={clarification}
              autoConfirmEnabled={clarificationAutoConfirmEnabled}
              language={language}
              onConfirm={(decision, target) => onClarificationDecision(message, decision, target)}
              onRequestInput={onRequestComposerFocus}
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
          </div>
        </>
      ) : (
        <div className="chat-user-bubble">{message.content}</div>
      )}
    </div>
  );
}

export default memo(ChatMessageItem);
