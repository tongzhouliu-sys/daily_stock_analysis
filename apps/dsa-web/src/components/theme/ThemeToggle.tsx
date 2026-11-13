import type React from 'react';
import { useEffect, useId, useRef, useState } from 'react';
import { Check, Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';

type ThemeOption = 'light' | 'dark' | 'system';
type ThemeToggleVariant = 'default' | 'nav' | 'rail';

const THEME_OPTIONS: Array<{
  value: ThemeOption;
  labelKey: UiTextKey;
  icon: typeof Sun;
}> = [
  { value: 'light', labelKey: 'theme.light', icon: Sun },
  { value: 'dark', labelKey: 'theme.dark', icon: Moon },
  { value: 'system', labelKey: 'theme.system', icon: Monitor },
];

function resolveThemeLabel(theme: string | undefined, t: (key: UiTextKey) => string) {
  switch (theme) {
    case 'light':
      return t('theme.light');
    case 'dark':
      return t('theme.dark');
    default:
      return t('theme.system');
  }
}

interface ThemeToggleProps {
  variant?: ThemeToggleVariant;
  collapsed?: boolean;
  iconOnly?: boolean;
  wrapperClassName?: string;
  triggerClassName?: string;
  triggerActiveClassName?: string;
  iconClassName?: string;
  labelClassName?: string;
}

export const ThemeToggle: React.FC<ThemeToggleProps> = ({
  variant = 'default',
  collapsed = false,
  iconOnly = false,
  wrapperClassName,
  triggerClassName,
  triggerActiveClassName,
  iconClassName,
  labelClassName,
}) => {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const { t } = useUiLanguage();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    menuRef.current?.querySelector<HTMLButtonElement>('[aria-checked=true]')?.focus();
    document.addEventListener('pointerdown', handlePointerDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [open]);

  const closeMenu = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const handleMenuKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
      return;
    }
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role=menuitemradio]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const nextIndex = {
      ArrowDown: (index + 1) % items.length,
      ArrowUp: (index - 1 + items.length) % items.length,
      Home: 0,
      End: items.length - 1,
    }[event.key];
    if (nextIndex === undefined) return;
    event.preventDefault();
    items[nextIndex]?.focus();
  };

  const activeTheme = (theme as ThemeOption | undefined) ?? 'system';
  const visualTheme = resolvedTheme ?? 'dark';
  const TriggerIcon = visualTheme === 'light' ? Sun : Moon;
  const isNavVariant = variant === 'nav';
  const isRailVariant = variant === 'rail';

  return (
    <div
      className={cn('relative', isRailVariant ? 'w-full' : '', wrapperClassName)}
      ref={containerRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setOpen((value) => !value)}
        data-state={open ? 'open' : 'closed'}
        className={cn(
          triggerClassName
            ? triggerClassName
            : isRailVariant
              ? 'flex h-[var(--nav-item-height)] w-full items-center justify-center gap-2.5 rounded-2xl border border-transparent px-2 text-sm leading-none text-secondary-text transition-all hover:bg-[var(--nav-hover-bg)] hover:text-foreground data-[state=open]:border-[var(--nav-active-border)] data-[state=open]:bg-[var(--nav-active-bg)] data-[state=open]:text-[hsl(var(--primary))]'
              : isNavVariant
                ? 'group relative flex h-12 w-full select-none items-center gap-3 rounded-[1.35rem] border border-transparent px-4 text-sm text-secondary-text transition-all duration-300 hover:bg-hover hover:text-foreground data-[state=open]:border-subtle data-[state=open]:bg-subtle data-[state=open]:text-foreground'
                : 'inline-flex h-10 items-center gap-2 rounded-xl border border-border/70 bg-card/80 px-3 text-sm text-secondary-text shadow-soft-card transition-colors hover:bg-hover hover:text-foreground',
          triggerClassName && open ? triggerActiveClassName : '',
          isNavVariant && collapsed ? 'justify-center px-2' : ''
        )}
        aria-haspopup="menu"
        aria-controls={open ? menuId : undefined}
        aria-expanded={open}
        aria-label={t('theme.toggle')}
      >
        <TriggerIcon className={iconClassName ?? cn('shrink-0', isRailVariant ? 'h-[18px] w-[18px]' : isNavVariant ? 'h-5 w-5' : 'h-4 w-4')} />
        {!iconOnly && (
          isRailVariant ? (
            <span className={labelClassName}>{t('theme.theme')}</span>
          ) : isNavVariant ? (
            collapsed ? null : <span className="truncate text-[1.02rem] font-medium">{t('theme.theme')}</span>
          ) : (
            <span className="hidden sm:inline">{resolveThemeLabel(activeTheme, t)}</span>
          )
        )}
      </button>

      {open ? (
        <div
          role="menu"
          id={menuId}
          ref={menuRef}
          onKeyDown={handleMenuKeyDown}
          aria-label={t('theme.menu')}
          className={cn(
            'z-[100] min-w-[8rem] border border-border bg-elevated p-1 shadow-lg',
            isNavVariant || isRailVariant
              ? 'absolute bottom-full left-0 mb-2 w-max min-w-[9rem]'
              : 'absolute right-0 mt-2'
          )}
        >
          {THEME_OPTIONS.map(({ value, labelKey, icon: Icon }) => {
            const isActive = activeTheme === value;
            return (
              <button
                key={value}
                type="button"
                role="menuitemradio"
                aria-checked={isActive}
                onClick={() => {
                  setTheme(value);
                  closeMenu();
                }}
                className={cn(
                  'flex w-full items-center justify-between rounded-xl px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'bg-cyan/10 text-foreground'
                    : 'text-secondary-text hover:bg-hover hover:text-foreground'
                )}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4" />
                  {t(labelKey)}
                </span>
                {isActive ? <Check className="h-4 w-4 text-cyan" /> : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
};
