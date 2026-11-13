import React from 'react';
import { Activity, BarChart3, Bell, BriefcaseBusiness, Database, Gauge, Home, MessageSquareQuote, Search, Settings2 } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';
import { StatusDot } from '../common/StatusDot';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
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

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default' }) => {
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const isRail = variant === 'rail';
  const itemClass = cn('research-nav-item shrink-0', collapsed && 'justify-center');
  const activeClass = 'research-nav-active font-medium';
  const iconClass = 'h-[17px] w-[17px] shrink-0';

  return (
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
  );
};
