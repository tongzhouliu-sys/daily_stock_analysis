import { PanelRightClose } from 'lucide-react';
import { cn } from '../../utils/cn';

export interface ChatOutlineItem {
  id: string;
  label: string;
  time: string;
}

interface ChatOutlineProps {
  items: ChatOutlineItem[];
  activeId: string | null;
  title: string;
  hideLabel: string;
  onSelect: (id: string) => void;
  onHide: () => void;
}

/**
 * Right-hand "question directory" for a multi-turn conversation (DeepSeek-style).
 * Pure presentation: the page owns the items, the active highlight and the jump.
 */
export default function ChatOutline({
  items,
  activeId,
  title,
  hideLabel,
  onSelect,
  onHide,
}: ChatOutlineProps) {
  return (
    <aside className="chat-outline" aria-label={title}>
      <div className="chat-outline-head">
        <span className="chat-outline-title">{title}</span>
        <button
          type="button"
          className="chat-icon-btn"
          aria-label={hideLabel}
          onClick={onHide}
        >
          <PanelRightClose size={15} aria-hidden="true" />
        </button>
      </div>
      <div className="chat-outline-scroll">
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            className={cn('chat-outline-item', item.id === activeId && 'is-active')}
            aria-current={item.id === activeId ? 'step' : undefined}
            onClick={() => onSelect(item.id)}
          >
            <span className="chat-outline-item-text">{item.label}</span>
            <span className="chat-outline-item-time">{item.time}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
