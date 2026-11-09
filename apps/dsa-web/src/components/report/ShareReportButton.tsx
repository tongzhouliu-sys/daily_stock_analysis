import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Check,
  FileDown,
  ImageDown,
  Link2,
  Loader2,
  Share2,
  TriangleAlert,
} from 'lucide-react';
import { historyApi } from '../../api/history';
import { getParsedApiError } from '../../api/error';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { buildReportPdf } from '../../utils/reportPdf';
import { buildReportLink, copyTextToClipboard } from '../../utils/reportShare';
import { Tooltip } from '../common/Tooltip';
import './share-report.css';

type DesktopWindow = Window & {
  dsaDesktop?: {
    renderShareImage?: (recordId: number) => Promise<ArrayBuffer>;
  };
};

type ShareAction = 'link' | 'pdf' | 'image';

interface FeedbackState {
  recordId?: number;
  kind: 'success' | 'error';
  message: string;
}

interface ShareReportButtonProps {
  recordId?: number;
  reportTitle: string;
  reportLanguage?: ReportLanguage;
  className?: string;
}

const safeFilenamePart = (value: string): string => {
  const normalized = value.trim().replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, '-');
  return normalized.slice(0, 72) || 'report';
};

const downloadBlob = (blob: Blob, filename: string): void => {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
};

/**
 * 报告分享入口。
 *
 * 三个动作按“依赖从轻到重”排列：
 * 1. 复制链接：纯前端，任何部署环境都可用；
 * 2. 导出 PDF：复用分享海报，在浏览器本地转 A4 PDF，不依赖后端转图工具；
 * 3. 分享图片：走 `/share-image` 或桌面端 IPC，失败时给出可执行的原因。
 */
export const ShareReportButton: React.FC<ShareReportButtonProps> = ({
  recordId,
  reportTitle,
  reportLanguage = 'zh',
  className = '',
}) => {
  const desktopRuntime = typeof window !== 'undefined' ? (window as DesktopWindow).dsaDesktop : undefined;
  const renderDesktopShareImage = desktopRuntime?.renderShareImage;
  const activeRecordId = desktopRuntime && !renderDesktopShareImage ? undefined : recordId;
  const text = getReportText(normalizeReportLanguage(reportLanguage));

  const [menuOpen, setMenuOpen] = useState(false);
  const [busyAction, setBusyAction] = useState<ShareAction | null>(null);
  /** 图片已生成、等待第二次点击以同步唤起系统分享面板。 */
  const [imageAwaitingShare, setImageAwaitingShare] = useState(false);
  const [feedbackSnapshot, setFeedbackSnapshot] = useState<FeedbackState | null>(null);
  const feedback = feedbackSnapshot && feedbackSnapshot.recordId === activeRecordId
    ? feedbackSnapshot
    : null;

  const containerRef = useRef<HTMLSpanElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const posterRef = useRef<{ recordId: number; blob: Blob } | null>(null);
  const loadTokenRef = useRef(0);
  const resetTimerRef = useRef<number | null>(null);

  const clearResetTimer = useCallback(() => {
    if (resetTimerRef.current !== null) {
      window.clearTimeout(resetTimerRef.current);
      resetTimerRef.current = null;
    }
  }, []);

  const setFeedback = useCallback((next: FeedbackState, autoDismiss = false) => {
    clearResetTimer();
    setFeedbackSnapshot(next);
    if (!autoDismiss) {
      return;
    }
    const scheduledRecordId = next.recordId;
    resetTimerRef.current = window.setTimeout(() => {
      resetTimerRef.current = null;
      setFeedbackSnapshot((current) => (
        current && current.recordId === scheduledRecordId ? null : current
      ));
    }, 2400);
  }, [clearResetTimer]);

  useEffect(() => {
    clearResetTimer();
    loadTokenRef.current += 1;
    posterRef.current = null;
    setMenuOpen(false);
    setBusyAction(null);
    setImageAwaitingShare(false);

    return () => {
      clearResetTimer();
      loadTokenRef.current += 1;
    };
  }, [activeRecordId, clearResetTimer]);

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }
    const firstItem = menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)');
    firstItem?.focus();

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Node && containerRef.current?.contains(target)) {
        return;
      }
      setMenuOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [menuOpen]);

  /** WAI-ARIA menu-button 模式：方向键在菜单项之间移动焦点。 */
  const handleMenuKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [],
    );
    if (items.length === 0) {
      return;
    }

    const move = (delta: number) => {
      const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
      const nextIndex = currentIndex === -1
        ? (delta > 0 ? 0 : items.length - 1)
        : (currentIndex + delta + items.length) % items.length;
      items[nextIndex]?.focus();
    };

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        move(1);
        break;
      case 'ArrowUp':
        event.preventDefault();
        move(-1);
        break;
      case 'Home':
        event.preventDefault();
        items[0]?.focus();
        break;
      case 'End':
        event.preventDefault();
        items[items.length - 1]?.focus();
        break;
      default:
        break;
    }
  }, []);

  const loadPoster = useCallback(async (recordIdValue: number): Promise<Blob> => {
    const cached = posterRef.current;
    if (cached && cached.recordId === recordIdValue) {
      return cached.blob;
    }
    const blob = renderDesktopShareImage
      ? new Blob([await renderDesktopShareImage(recordIdValue)], { type: 'image/png' })
      : await historyApi.getShareImage(recordIdValue);
    posterRef.current = { recordId: recordIdValue, blob };
    return blob;
  }, [renderDesktopShareImage]);

  const handleCopyLink = useCallback(async () => {
    if (activeRecordId === undefined || busyAction) return;
    setBusyAction('link');
    try {
      const copied = await copyTextToClipboard(buildReportLink(activeRecordId));
      setFeedback({
        recordId: activeRecordId,
        kind: copied ? 'success' : 'error',
        message: copied ? text.shareLinkCopied : `${text.shareLinkCopyFailed}: ${buildReportLink(activeRecordId)}`,
      }, copied);
      if (copied) {
        setMenuOpen(false);
      }
    } finally {
      setBusyAction(null);
    }
  }, [activeRecordId, busyAction, setFeedback, text.shareLinkCopyFailed, text.shareLinkCopied]);

  const handleExportPdf = useCallback(async () => {
    if (activeRecordId === undefined || busyAction) return;
    clearResetTimer();
    setFeedbackSnapshot(null);
    setBusyAction('pdf');
    try {
      const poster = await loadPoster(activeRecordId);
      const pdf = await buildReportPdf(poster);
      downloadBlob(pdf, `${safeFilenamePart(reportTitle)}-${activeRecordId}.pdf`);
      setFeedback({
        recordId: activeRecordId,
        kind: 'success',
        message: text.sharePdfReady,
      }, true);
      setMenuOpen(false);
    } catch (error) {
      console.error('Export share PDF failed:', error);
      setFeedback({
        recordId: activeRecordId,
        kind: 'error',
        message: `${text.sharePdfFailed}: ${getParsedApiError(error).message}`,
      });
    } finally {
      setBusyAction(null);
    }
  }, [
    activeRecordId,
    busyAction,
    clearResetTimer,
    loadPoster,
    reportTitle,
    setFeedback,
    text.sharePdfFailed,
    text.sharePdfReady,
  ]);

  const handleShareImage = useCallback(async () => {
    if (activeRecordId === undefined || busyAction) return;
    clearResetTimer();
    setFeedbackSnapshot(null);

    let blob = posterRef.current?.recordId === activeRecordId ? posterRef.current.blob : null;
    let generatedNow = false;

    if (!blob) {
      const loadToken = loadTokenRef.current + 1;
      loadTokenRef.current = loadToken;
      setBusyAction('image');
      try {
        blob = await loadPoster(activeRecordId);
      } catch (error) {
        if (loadTokenRef.current !== loadToken) return;
        console.error('Generate share image failed:', error);
        setFeedback({
          recordId: activeRecordId,
          kind: 'error',
          message: getParsedApiError(error).message,
        });
        setBusyAction(null);
        return;
      }
      if (loadTokenRef.current !== loadToken) return;
      generatedNow = true;
      setBusyAction(null);
    }

    const filename = `${safeFilenamePart(reportTitle)}-${activeRecordId}.png`;
    const file = new File([blob], filename, { type: 'image/png' });
    const canShareFile = typeof navigator.share === 'function'
      && typeof navigator.canShare === 'function'
      && navigator.canShare({ files: [file] });

    // A file cannot be shared before it exists, while navigator.share() must run
    // inside a transient user-activation event. Prepare on the first click and
    // let the next click invoke native sharing synchronously.
    if (generatedNow && canShareFile) {
      setImageAwaitingShare(true);
      return;
    }

    setBusyAction('image');
    try {
      if (canShareFile) {
        try {
          await navigator.share({
            files: [file],
            title: reportTitle,
          });
        } catch (error) {
          if (error instanceof DOMException && error.name === 'AbortError') {
            setImageAwaitingShare(true);
            return;
          }
          console.warn('Native file sharing failed; falling back to download:', error);
          downloadBlob(blob, filename);
        }
      } else {
        downloadBlob(blob, filename);
      }

      setImageAwaitingShare(false);
      setFeedback({
        recordId: activeRecordId,
        kind: 'success',
        message: text.shareImageReady,
      }, true);
      setMenuOpen(false);
    } catch (error) {
      console.error('Generate share image failed:', error);
      setFeedback({
        recordId: activeRecordId,
        kind: 'error',
        message: getParsedApiError(error).message,
      });
    } finally {
      setBusyAction(null);
    }
  }, [
    activeRecordId,
    busyAction,
    clearResetTimer,
    loadPoster,
    reportTitle,
    setFeedback,
    text.shareImageReady,
  ]);

  if (activeRecordId === undefined) return null;

  const isBusy = busyAction !== null;
  const triggerLabel = busyAction === 'pdf'
    ? text.sharePreparingPdf
    : busyAction
      ? text.generatingShareImage
      : text.generateShareImage;
  const imageItemLabel = imageAwaitingShare ? text.shareImageReadyToShare : text.shareExportImage;

  return (
    <span ref={containerRef} className={`report-share ${className}`}>
      <Tooltip content={text.shareMenuLabel}>
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          className="home-surface-button flex h-10 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 text-sm font-medium text-secondary-text hover:text-foreground"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={text.shareMenuLabel}
        >
          {isBusy ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" /> : null}
          {imageAwaitingShare && !isBusy ? <Check className="h-5 w-5 text-success" aria-hidden="true" /> : null}
          {!isBusy && !imageAwaitingShare ? <Share2 className="h-5 w-5" aria-hidden="true" /> : null}
          <span>{triggerLabel}</span>
        </button>
      </Tooltip>

      {menuOpen ? (
        <div
          ref={menuRef}
          className="report-share-menu"
          role="menu"
          aria-label={text.shareMenuLabel}
          onKeyDown={handleMenuKeyDown}
        >
          <button
            type="button"
            role="menuitem"
            className="report-share-menu-item"
            onClick={() => void handleCopyLink()}
            disabled={isBusy}
          >
            <Link2 className="h-4 w-4" aria-hidden="true" />
            <span>{text.shareCopyLink}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="report-share-menu-item"
            onClick={() => void handleExportPdf()}
            disabled={isBusy}
          >
            <FileDown className="h-4 w-4" aria-hidden="true" />
            <span>{text.shareExportPdf}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="report-share-menu-item"
            onClick={() => void handleShareImage()}
            disabled={isBusy}
          >
            <ImageDown className="h-4 w-4" aria-hidden="true" />
            <span>{imageItemLabel}</span>
          </button>
        </div>
      ) : null}

      {feedback ? (
        <span
          role={feedback.kind === 'error' ? 'alert' : 'status'}
          className={feedback.kind === 'error' ? 'report-share-error' : 'report-share-status'}
        >
          {feedback.kind === 'error' ? <TriangleAlert className="h-3 w-3" aria-hidden="true" /> : null}
          {feedback.message}
        </span>
      ) : null}
    </span>
  );
};
