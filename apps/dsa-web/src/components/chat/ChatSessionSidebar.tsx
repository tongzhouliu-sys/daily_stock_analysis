import { Check, MoreHorizontal, PanelLeftClose, Pin, Plus, Search, X } from 'lucide-react';
import { DashboardStateBlock } from '../dashboard';
import { ScrollArea } from '../common';
import type { TranslateFn } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { formatChatTimestamp } from '../../utils/chatTime';

export type SessionMenuOrigin = 'sidebar' | 'header';

export interface ChatSidebarSession {
  session_id: string;
  title: string;
  is_pinned: boolean;
  message_count?: number;
  last_active?: string | null;
  created_at?: string | null;
}

export interface ChatSidebarGroup {
  key: string;
  label: string;
  items: ChatSidebarSession[];
}

interface ChatSessionSidebarProps {
  t: TranslateFn;
  language: 'zh' | 'en';
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
  /**
   * `desktop` renders the in-panel header (new chat + collapse) and owns its
   * scroll area. `mobile` lives inside the history drawer, which already
   * provides the title row, close button and scrolling.
   */
  variant?: 'desktop' | 'mobile';
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  searchScopeHint?: string;
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
 * presentational shell so the page render tree stays small. Each row carries
 * two lines (title, then "last active · message count") so that conversations
 * sharing a title or topical wording stay distinguishable.
 */
export default function ChatSessionSidebar({
  t,
  language,
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
  variant = 'desktop',
  searchValue = '',
  onSearchChange,
  searchScopeHint,
  onStartNewChat,
  onToggleSidebar,
  onSwitchSession,
  onChangeTitle,
  onSaveTitle,
  onCloseTitleEditor,
  onOpenMenu,
}: ChatSessionSidebarProps) {
  const isMobilePanel = variant === 'mobile';
  const visibleCount = sessionGroups.reduce((total, group) => total + group.items.length, 0);

  const renderRowMeta = (session: ChatSidebarSession) => {
    const timestamp = formatChatTimestamp(session.last_active || session.created_at || null, language);
    const parts = [timestamp.short, t('chat.sessionMessages', { count: session.message_count ?? 0 })]
      .filter((part) => part && part.length > 0);
    return parts.join(' · ');
  };

  const list = (
    <>
      {sessionsLoading ? (
        <DashboardStateBlock
          loading
          compact
          title={t('common.loading')}
          className="chat-sessions-state"
        />
      ) : sessionsCount === 0 ? (
        <DashboardStateBlock
          compact
          title={t('chat.noHistoryTitle')}
          description={t('chat.noHistoryDescription')}
          className="chat-sessions-state"
        />
      ) : visibleCount === 0 ? (
        <DashboardStateBlock
          compact
          title={t('chat.searchEmpty')}
          className="chat-sessions-state"
        />
      ) : (
        sessionGroups.map((group) => (
          <div key={group.key} className="chat-session-group">
            {group.label ? <p className="chat-session-group-label">{group.label}</p> : null}
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
                        aria-label={`${t('chat.switchSession')} ${session.title}`}
                        aria-current={session.session_id === sessionId ? 'page' : undefined}
                      >
                        <span className="chat-session-line">
                          {session.is_pinned ? (
                            <span className="chat-session-pin" aria-hidden="true">
                              <Pin size={12} />
                            </span>
                          ) : null}
                          <span className="chat-session-title">{session.title}</span>
                        </span>
                        <span className="chat-session-meta">{renderRowMeta(session)}</span>
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
    </>
  );

  if (isMobilePanel) {
    return (
      <>
        <button type="button" className="chat-new-btn chat-new-btn-panel" onClick={onStartNewChat}>
          <Plus size={15} aria-hidden="true" />
          <span>{t('chat.newChat')}</span>
        </button>
        <label className="chat-session-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={searchValue}
            onChange={(event) => onSearchChange?.(event.target.value)}
            placeholder={t('chat.searchSessions')}
            aria-label={t('chat.searchSessions')}
          />
        </label>
        {searchScopeHint ? <p className="chat-session-search-hint">{searchScopeHint}</p> : null}
        <div className="chat-sessions-mobile-list" data-testid="chat-session-list-scroll">
          {list}
        </div>
      </>
    );
  }

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
        {list}
      </ScrollArea>
    </>
  );
}
