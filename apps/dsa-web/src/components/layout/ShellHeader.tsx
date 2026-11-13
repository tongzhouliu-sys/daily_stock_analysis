import { useState } from 'react';
import { Info, LogOut, Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { DesktopUpdateIndicator } from './DesktopUpdateIndicator';
import { ResearchMark } from './ResearchIdentity';

const ROUTE_LABELS: Record<string, UiTextKey> = {
  '/': 'layout.route.home.title',
  '/chat': 'layout.route.chat.title',
  '/portfolio': 'layout.route.portfolio.title',
  '/screening': 'layout.route.screening.title',
  '/scanner': 'layout.nav.scanner',
  '/backtest': 'layout.route.backtest.title',
  '/alerts': 'layout.route.alerts.title',
  '/decision-signals': 'layout.route.decisionSignals.title',
  '/data': 'layout.route.dataCenter.title',
  '/usage': 'layout.route.usage.title',
  '/settings': 'layout.route.settings.title',
};

type ShellHeaderProps = {
  onOpenMobileNav: () => void;
  navCollapsed?: boolean;
  onToggleNav?: () => void;
};

export function ShellHeader({ onOpenMobileNav, navCollapsed = false, onToggleNav }: ShellHeaderProps) {
  const { pathname } = useLocation();
  const { t } = useUiLanguage();
  const { authEnabled, logout } = useAuth();
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const routeLabel = pathname.startsWith('/scanner/')
    ? 'layout.nav.scanner' : ROUTE_LABELS[pathname.replace(/\/$/, '') || '/'];

  return (
    <header className="research-masthead">
      <a className="research-skip-link" href="#workspace">{t('layout.skipToContent')}</a>
      <button
        type="button"
        onClick={onOpenMobileNav}
        className="research-mobile-menu lg:hidden"
        aria-label={t('layout.openNav')}
      >
        <Menu size={20} aria-hidden="true" />
      </button>
      <Link to="/" className="research-brand" aria-label={t('layout.appFallbackTitle')}>
        <ResearchMark />
        <span>
          <strong>DSA</strong>
          <span className="research-brand-name">Daily Stock Analysis</span>
        </span>
      </Link>
      {onToggleNav ? (
        <button
          type="button"
          className="research-nav-toggle"
          onClick={onToggleNav}
          aria-label={t(navCollapsed ? 'layout.expandSidebar' : 'layout.collapseSidebar')}
          aria-expanded={!navCollapsed}
        >
          {navCollapsed
            ? <PanelLeftOpen size={18} aria-hidden="true" />
            : <PanelLeftClose size={18} aria-hidden="true" />}
        </button>
      ) : null}
      <div className="research-location">
        <span>{t('layout.workspace')}</span>
        <span className="text-muted-text" aria-hidden="true">/</span>
        <span className="font-medium text-foreground">{t(routeLabel ?? 'notFound.title')}</span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <DesktopUpdateIndicator />
        <div className="flex items-center gap-1">
          <UiLanguageToggle iconOnly />
          <ThemeToggle iconOnly />
          <div className="relative group">
            <button
              type="button"
              className="inline-flex h-10 items-center justify-center rounded-xl border border-border/70 bg-card/80 px-3 text-sm text-secondary-text shadow-soft-card transition-colors hover:bg-hover hover:text-foreground"
              aria-label={t('layout.about')}
            >
              <Info className="h-4 w-4" />
            </button>
            <div className="pointer-events-none absolute top-full right-0 mt-2 whitespace-nowrap rounded-lg border border-border bg-elevated px-3 py-2 text-sm text-foreground opacity-0 shadow-lg transition-opacity group-hover:pointer-events-auto group-hover:opacity-100">
              {t('layout.copyright')}
            </div>
          </div>
          {authEnabled ? (
            <button
              type="button"
              onClick={() => setShowLogoutConfirm(true)}
              className="inline-flex h-10 items-center gap-2 rounded-xl border border-border/70 bg-card/80 px-3 text-sm text-secondary-text shadow-soft-card transition-colors hover:bg-hover hover:text-foreground"
              aria-label={t('layout.logout')}
            >
              <LogOut className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </div>
      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title={t('layout.logoutTitle')}
        message={t(/^\/settings\/?$/.test(pathname) ? 'layout.logoutSettingsMessage' : 'layout.logoutMessage')}
        confirmText={t('layout.logoutConfirm')}
        cancelText={t('common.cancel')}
        isDanger
        onConfirm={() => {
          setShowLogoutConfirm(false);
          void logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </header>
  );
}
