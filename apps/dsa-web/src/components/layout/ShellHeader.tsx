import { Menu } from 'lucide-react';
import { Link, useLocation } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';
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

export function ShellHeader({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const { pathname } = useLocation();
  const { t } = useUiLanguage();
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
      <div className="research-location">
        <span>{t('layout.workspace')}</span>
        <span className="text-muted-text" aria-hidden="true">/</span>
        <span className="font-medium text-foreground">{t(routeLabel ?? 'notFound.title')}</span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <DesktopUpdateIndicator />
        <div className="flex items-center gap-1 lg:hidden">
          <UiLanguageToggle />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
