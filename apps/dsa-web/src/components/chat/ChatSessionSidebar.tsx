import { Check, MoreHorizontal, PanelLeftClose, Plus, X } from 'lucide-react';
import { DashboardStateBlock } from '../dashboard';
import { ScrollArea } from '../common';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';

export type SessionMenuOrigin = 'sidebar' | 'header';

export interface ChatSidebarSession {
  session_id: string;
  title: string;
}

export interface ChatSidebarGroup {
  key: string;
  label: string;
  items: ChatSidebarSession[];
}

interface ChatSessionSidebarProps {
  t: TranslateFn;
  sessionGroups: ChatSidebarGroup[];
  sessionsCount: number;
  sessionsLoading: boolean;
  sessionId: string | null;
  menuSessionId: string | null;
  editingSessionId: string | null;
  titleEditorOrigin: SessionMenuOrigin | null;
  sessionTitle: string;
  titleSaving: boolean;
  titleError: string;
  onStartNewChat: () => void;
  onToggleSidebar: () => void;
  onSwitchSession: (sessionId: string) => void;
  onChangeTitle: (value: string) => void;
  onSaveTitle: () => void;
  onCloseTitleEditor: () => void;
  onOpenMenu: (event: React.MouseEvent<HTMLButtonElement>, sessionId: string) => void;
}

/**
 * Conversation sidebar extracted from ChatPage (T40).
 *
 * The page keeps the session-menu popover and rename state; this component is a
 * presentational shell so the page render tree stays small.
 */
export default function ChatSessionSidebar({
  t,
  sessionGroups,
  sessionsCount,
  sessionsLoading,
  sessionId,
  menuSessionId,
  editingSessionId,
  titleEditorOrigin,
  sessionTitle,
  titleSaving,
  titleError,
  onStartNewChat,
  onToggleSidebar,
  onSwitchSession,
  onChangeTitle,
  onSaveTitle,
  onCloseTitleEditor,
  onOpenMenu,
}: ChatSessionSidebarProps) {
  return (
    <>
      <div className="chat-sessions-head">
        <button type="button" className="chat-new-btn" onClick={onStartNewChat}>
          <Plus size={15} aria-hidden="true" />
          <span>{t('chat.newChat')}</span>
        </button>
        <button
          type="button"
          className="chat-icon-btn chat-sidebar-collapse"
          aria-label={t('chat.hideHistory')}
          onClick={onToggleSidebar}
        >
          <PanelLeftClose size={16} aria-hidden="true" />
        </button>
      </div>
      <ScrollArea testId="chat-session-list-scroll" className="chat-sessions-scroll" viewportClassName="chat-sessions-viewport">
        {sessionsLoading ? (
          <DashboardStateBlock
            loading
            compact
            title="加载对话中..."
            className="chat-sessions-state"
          />
        ) : sessionsCount === 0 ? (
          <DashboardStateBlock
            compact
            title="暂无历史对话"
            description="开始提问后，这里会保留会话记录。"
            className="chat-sessions-state"
          />
        ) : (
          sessionGroups.map((group) => (
            <div key={group.key} className="chat-session-group">
              <p className="chat-session-group-label">{group.label}</p>
              <div className="chat-session-list">
                {group.items.map((session) => (
                  <div key={session.session_id} className="chat-session-row">
                    {editingSessionId === session.session_id && titleEditorOrigin === 'sidebar' ? (
                      <div className="chat-title-editor">
                        <form onSubmit={(event) => { event.preventDefault(); onSaveTitle(); }}>
                          <input
                            aria-label={t('chat.editTitle')}
                            value={sessionTitle}
                            maxLength={60}
                            autoFocus
                            onChange={(event) => onChangeTitle(event.target.value)}
                            onKeyDown={(event) => { if (event.key === 'Escape') onCloseTitleEditor(); }}
                          />
                          <button type="submit" disabled={titleSaving} aria-label={t('chat.saveTitle')}>
                            <Check size={15} aria-hidden="true" />
                          </button>
                          <button type="button" disabled={titleSaving} onClick={onCloseTitleEditor} aria-label={t('common.cancel')}>
                            <X size={15} aria-hidden="true" />
                          </button>
                        </form>
                        {titleError && <p role="alert">{titleError}</p>}
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => onSwitchSession(session.session_id)}
                          className={cn('chat-session', session.session_id === sessionId && 'is-active')}
                          aria-label={`切换到对话 ${session.title}`}
                          aria-current={session.session_id === sessionId ? 'page' : undefined}
                        >
                          <span className="chat-session-title">{session.title}</span>
                        </button>
                        <button
                          type="button"
                          className="chat-session-more"
                          aria-label={`${t('chat.moreActions')} ${session.title}`}
                          aria-haspopup="menu"
                          aria-expanded={menuSessionId === session.session_id}
                          onClick={(event) => onOpenMenu(event, session.session_id)}
                        >
                          <MoreHorizontal size={15} aria-hidden="true" />
                        </button>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </ScrollArea>
    </>
  );
}
