import { useCallback, useEffect, useId, useRef } from 'react';
import type React from 'react';
import { X } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  zIndex?: number;
  testId?: string;
}

/**
 * Phone action sheet: rises from the bottom edge, keeps the surface above the
 * on-screen keyboard safe area, and closes on Escape, backdrop tap or the close
 * button. Desktop keeps its own popovers; this component only renders when the
 * caller decides the compact layout is active.
 */
export function BottomSheet({
  isOpen,
  onClose,
  title,
  children,
  zIndex = 70,
  testId,
}: BottomSheetProps) {
  const { t } = useUiLanguage();
  const titleId = useId();
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  const handleKeyDown = useCallback((event: KeyboardEvent) => {
    if (event.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const previouslyFocused = document.activeElement;
    closeButtonRef.current?.focus({ preventScroll: true });
    document.addEventListener('keydown', handleKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0" style={{ zIndex }} role="presentation" data-testid={testId}>
      <div className="absolute inset-0 bg-black/45" onClick={onClose} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        className="absolute inset-x-0 bottom-0 max-h-[80vh] overflow-y-auto rounded-t-2xl border-t border-border bg-card px-4 pt-3 shadow-2xl"
        style={{ paddingBottom: 'max(16px, env(safe-area-inset-bottom))' }}
      >
        <div className="mb-2 flex items-center justify-between gap-3">
          {title ? (
            <h2 id={titleId} className="chat-sheet-title">{title}</h2>
          ) : <span />}
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            className="chat-icon-btn"
            aria-label={t('common.close')}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export default BottomSheet;
