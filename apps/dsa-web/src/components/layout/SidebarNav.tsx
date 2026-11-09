import React, { useState } from 'react';
import { Activity, BarChart3, Bell, BriefcaseBusiness, Database, Gauge, Home, LogOut, MessageSquareQuote, Search, Settings2 } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { StatusDot } from '../common/StatusDot';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
  showPreferences?: boolean;
};

type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  groupLabel?: UiTextKey;
  badge?: 'completion';
};

const NAV_ITEMS: NavItem[] = [
  { key: 'home', groupLabel: 'layout.group.research', labelKey: 'layout.nav.home', to: '/', icon: Home, exact: true },
  { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion' },
  { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search },
  { key: 'scanner', labelKey: 'layout.nav.scanner', to: '/scanner', icon: Activity },
  { key: 'portfolio', groupLabel: 'layout.group.monitor', labelKey: 'layout.nav.portfolio', to: '/portfolio', icon: BriefcaseBusiness },
  { key: 'decision-signals', labelKey: 'layout.nav.decisionSignals', to: '/decision-signals', icon: Activity },
  { key: 'backtest', labelKey: 'layout.nav.backtest', to: '/backtest', icon: BarChart3 },
  { key: 'alerts', labelKey: 'layout.nav.alerts', to: '/alerts', icon: Bell },
  { key: 'data', groupLabel: 'layout.group.system', labelKey: 'layout.nav.dataCenter', to: '/data', icon: Database },
  { key: 'usage', labelKey: 'layout.nav.usage', to: '/usage', icon: Gauge },
  { key: 'settings', labelKey: 'layout.nav.settings', to: '/settings', icon: Settings2 },
];

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default', showPreferences = true }) => {
  const { authEnabled, logout } = useAuth();
  const location = useLocation();
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const isRail = variant === 'rail';
  const itemClass = cn('research-nav-item shrink-0', collapsed && 'justify-center');
  const activeClass = 'research-nav-active font-medium';
  const iconClass = 'h-[17px] w-[17px] shrink-0';

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <nav className="research-nav-list min-h-0 flex-1 overflow-y-auto" aria-label={t('layout.mainNav')}>
        {NAV_ITEMS.map(({ key, groupLabel, labelKey, to, icon: Icon, exact, badge }) => (
          <React.Fragment key={key}>
            {groupLabel && !collapsed ? <p className="research-nav-group">{t(groupLabel)}</p> : null}
            <NavLink
              to={to}
              end={exact}
              onClick={onNavigate}
              aria-label={t(labelKey)}
              className={({ isActive }) => cn(itemClass, isActive && activeClass)}
            >
              <Icon className={iconClass} aria-hidden="true" />
              {!collapsed ? <span className="truncate">{t(labelKey)}</span> : null}
              {badge === 'completion' && completionBadge ? (
                <StatusDot
                  tone="info"
                  data-testid="chat-completion-badge"
                  className="absolute right-3"
                  aria-label={t('layout.newChatMessage')}
                />
              ) : null}
            </NavLink>
          </React.Fragment>
        ))}
      </nav>

      {showPreferences || authEnabled ? (
        <div className="research-nav-controls">
          {showPreferences ? (
            <>
              <ThemeToggle
                variant={isRail ? 'rail' : 'nav'}
                collapsed={collapsed}
                triggerClassName={itemClass}
                triggerActiveClassName={activeClass}
                iconClassName={iconClass}
              />
              <UiLanguageToggle
                variant={isRail ? 'rail' : 'nav'}
                collapsed={collapsed}
                triggerClassName={itemClass}
                iconClassName={iconClass}
              />
            </>
          ) : null}
          {authEnabled ? (
            <button
              type="button"
              onClick={() => setShowLogoutConfirm(true)}
              className={cn(itemClass, 'cursor-pointer')}
              aria-label={t('layout.logout')}
            >
              <LogOut className={iconClass} aria-hidden="true" />
              {!collapsed ? <span className="truncate">{t('layout.logout')}</span> : null}
            </button>
          ) : null}
        </div>
      ) : null}

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title={t('layout.logoutTitle')}
        message={t(/^\/settings\/?$/.test(location.pathname) ? 'layout.logoutSettingsMessage' : 'layout.logoutMessage')}
        confirmText={t('layout.logoutConfirm')}
        cancelText={t('common.cancel')}
        isDanger
        onConfirm={() => {
          setShowLogoutConfirm(false);
          onNavigate?.();
          void logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </div>
  );
};
