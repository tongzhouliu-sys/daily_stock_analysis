import type React from 'react';
import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { ShellHeader } from './ShellHeader';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { t } = useUiLanguage();

  useEffect(() => {
    if (!mobileOpen) return;

    const desktop = window.matchMedia('(min-width: 64rem)');
    const closeOnDesktop = () => {
      if (desktop.matches) setMobileOpen(false);
    };
    desktop.addEventListener('change', closeOnDesktop);
    return () => desktop.removeEventListener('change', closeOnDesktop);
  }, [mobileOpen]);

  return (
    <div className="research-shell">
      <ShellHeader onOpenMobileNav={() => setMobileOpen(true)} />
      <div className="research-shell-body">
        <aside className="research-sidebar" aria-label={t('layout.desktopSidebar')}>
          <SidebarNav variant="rail" />
        </aside>
        <main id="workspace" tabIndex={-1} className="min-w-0">
          {children ?? <Outlet />}
        </main>
      </div>

      <Drawer
        isOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        title={t('layout.navMenu')}
        width="max-w-xs"
        zIndex={90}
        side="left"
      >
        <SidebarNav onNavigate={() => setMobileOpen(false)} showPreferences={false} />
      </Drawer>
    </div>
  );
};
