import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { historyApi } from '../../api/history';
import { getParsedApiError } from '../../api/error';
import { getReportText } from '../../utils/reportLanguage';
import { Drawer } from '../common/Drawer';

interface ReportHtmlDrawerProps {
  recordId: number;
  reportTitle: string;
  onClose: () => void;
}

type LoadState =
  | { status: 'loading'; recordId: number }
  | { status: 'ready'; recordId: number; html: string }
  | { status: 'missing'; recordId: number }
  | { status: 'generating'; recordId: number }
  | { status: 'error'; recordId: number; message: string };

/**
 * HTML 报告预览抽屉。
 *
 * 用 sandbox iframe 展示服务端返回的 HTML：只给 allow-popups（正文外链在
 * 新标签打开），不给 allow-scripts / allow-same-origin，确保其无法操作宿主
 * 页面或读取 DOM。切换记录时通过 token 作废旧的加载结果，避免串页。
 */
export const ReportHtmlDrawer: React.FC<ReportHtmlDrawerProps> = ({
  recordId,
  reportTitle,
  onClose,
}) => {
  // HTML 报告固定中文：抽屉文案不跟随记录语言或界面语言。
  const text = getReportText('zh');
  const [isOpen, setIsOpen] = useState(true);
  const [state, setState] = useState<LoadState>({ status: 'loading', recordId });
  const loadTokenRef = useRef(0);

  useEffect(() => {
    const token = loadTokenRef.current + 1;
    loadTokenRef.current = token;
    let active = true;
    historyApi.getReportHtml(recordId)
      .then((html) => {
        if (active && loadTokenRef.current === token) {
          setState({ status: 'ready', recordId, html });
        }
      })
      .catch((error: unknown) => {
        if (!active || loadTokenRef.current !== token) return;
        const parsed = getParsedApiError(error);
        if (parsed.code === 'zh_variant_missing') {
          setState({ status: 'missing', recordId });
          return;
        }
        setState({ status: 'error', recordId, message: parsed.message });
      });
    return () => {
      active = false;
      loadTokenRef.current += 1;
    };
  }, [recordId]);

  const handleGenerateChinese = useCallback(async () => {
    const token = loadTokenRef.current + 1;
    loadTokenRef.current = token;
    setState({ status: 'generating', recordId });
    try {
      await historyApi.ensureReportZhVariant(recordId);
      const html = await historyApi.getReportHtml(recordId);
      if (loadTokenRef.current === token) {
        setState({ status: 'ready', recordId, html });
      }
    } catch (error: unknown) {
      if (loadTokenRef.current !== token) return;
      setState({
        status: 'error',
        recordId,
        message: `${text.zhVariantFailed}: ${getParsedApiError(error).message}`,
      });
    }
  }, [recordId, text.zhVariantFailed]);

  const handleClose = useCallback(() => {
    setIsOpen(false);
    window.setTimeout(onClose, 300);
  }, [onClose]);

  // 只有与当前记录匹配的结果才展示，避免切换记录瞬间显示旧内容。
  const currentState: LoadState = state.recordId === recordId ? state : { status: 'loading', recordId };

  return (
    <Drawer
      isOpen={isOpen}
      onClose={handleClose}
      width="max-w-4xl"
      zIndex={100}
      backdropClassName="bg-background/56 backdrop-blur-[2px]"
    >
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <h2 className="truncate text-base font-semibold text-foreground">
            {text.htmlReportTitle} · {reportTitle}
          </h2>
          <button
            type="button"
            onClick={handleClose}
            className="home-surface-button shrink-0 rounded-lg px-3 py-1.5 text-sm text-secondary-text"
          >
            {text.htmlReportClose}
          </button>
        </div>
        <div className="min-h-0 flex-1">
          {currentState.status === 'loading' ? (
            <div className="flex h-64 flex-col items-center justify-center">
              <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
              <p className="mt-4 text-sm text-secondary-text">{text.loadingReport}</p>
            </div>
          ) : null}
          {currentState.status === 'generating' ? (
            <div className="flex h-64 flex-col items-center justify-center">
              <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
              <p className="mt-4 text-sm text-secondary-text">{text.generatingZhVariant}</p>
            </div>
          ) : null}
          {currentState.status === 'missing' ? (
            <div className="flex h-64 flex-col items-center justify-center px-6 text-center">
              <p className="text-sm text-secondary-text">{text.zhVariantMissing}</p>
              <button
                type="button"
                onClick={() => void handleGenerateChinese()}
                className="home-surface-button mt-4 rounded-lg px-4 py-2 text-sm text-secondary-text"
              >
                {text.generateZhVariant}
              </button>
            </div>
          ) : null}
          {currentState.status === 'error' ? (
            <div className="flex h-64 flex-col items-center justify-center">
              <p className="text-sm text-danger">{currentState.message}</p>
              <button
                type="button"
                onClick={handleClose}
                className="home-surface-button mt-4 rounded-lg px-4 py-2 text-sm text-secondary-text"
              >
                {text.dismiss}
              </button>
            </div>
          ) : null}
          {currentState.status === 'ready' ? (
            <iframe
              title={text.htmlReportTitle}
              srcDoc={currentState.html}
              sandbox="allow-popups allow-popups-to-escape-sandbox"
              className="h-full w-full border-0 bg-background"
            />
          ) : null}
        </div>
      </div>
    </Drawer>
  );
};
