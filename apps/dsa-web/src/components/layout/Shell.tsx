import type React from 'react';
import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { Drawer } from '../common/Drawer';
import { SidebarNav } from './SidebarNav';
import { ShellHeader } from './ShellHeader';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

const NAV_COLLAPSE_STORAGE_KEY = 'dsa_shell_nav_collapsed';

const readNavCollapsed = (): boolean => {
  if (typeof localStorage === 'undefined') return false;
  try {
    return localStorage.getItem(NAV_COLLAPSE_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
};

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [navCollapsed, setNavCollapsed] = useState(readNavCollapsed);
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

  const toggleNav = () => {
    setNavCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(NAV_COLLAPSE_STORAGE_KEY, String(next));
      } catch {
        // Storage may be unavailable; the in-memory state still applies.
      }
      return next;
    });
  };

  return (
    <div className="research-shell" data-nav-collapsed={navCollapsed ? 'true' : 'false'}>
      <ShellHeader
        onOpenMobileNav={() => setMobileOpen(true)}
        navCollapsed={navCollapsed}
        onToggleNav={toggleNav}
      />
      <div className="research-shell-body">
        <aside className="research-sidebar" aria-label={t('layout.desktopSidebar')}>
          <SidebarNav variant="rail" collapsed={navCollapsed} />
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
