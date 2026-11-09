import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { buildReportPdf } from '../../../utils/reportPdf';
import { ShareReportButton } from '../ShareReportButton';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getShareImage: vi.fn(),
  },
}));

vi.mock('../../../utils/reportPdf', () => ({
  buildReportPdf: vi.fn(),
}));

const mockedGetShareImage = vi.mocked(historyApi.getShareImage);
const mockedBuildReportPdf = vi.mocked(buildReportPdf);

const openMenu = () => fireEvent.click(screen.getByRole('button', { name: '分享报告' }));
const menuItem = (name: string) => screen.getByRole('menuitem', { name });

describe('ShareReportButton', () => {
  beforeEach(() => {
    vi.useRealTimers();
    mockedGetShareImage.mockReset();
    mockedBuildReportPdf.mockReset();
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:share-image');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    Object.defineProperty(window, 'dsaDesktop', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'share', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: undefined });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('offers link, PDF and image actions without generating anything up front', () => {
    render(<ShareReportButton recordId={17} reportTitle="中钨高新-000657" reportLanguage="zh" />);

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(mockedGetShareImage).not.toHaveBeenCalled();
    expect(mockedBuildReportPdf).not.toHaveBeenCalled();

    openMenu();

    expect(menuItem('复制链接')).toBeInTheDocument();
    expect(menuItem('导出 PDF')).toBeInTheDocument();
    expect(menuItem('分享图片')).toBeInTheDocument();
    expect(mockedGetShareImage).not.toHaveBeenCalled();
  });

  it('copies the report deep link without calling the image backend', async () => {
    render(<ShareReportButton recordId={17} reportTitle="中钨高新-000657" reportLanguage="zh" />);
    openMenu();

    fireEvent.click(menuItem('复制链接'));

    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledTimes(1));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      new URL('/?recordId=17', window.location.origin).toString(),
    );
    expect(await screen.findByRole('status')).toHaveTextContent('链接已复制');
    expect(mockedGetShareImage).not.toHaveBeenCalled();
  });

  it('exports a PDF from the share poster and downloads it', async () => {
    mockedGetShareImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    mockedBuildReportPdf.mockResolvedValue(new Blob(['pdf'], { type: 'application/pdf' }));

    render(<ShareReportButton recordId={17} reportTitle="中钨高新-000657" reportLanguage="zh" />);
    openMenu();
    fireEvent.click(menuItem('导出 PDF'));

    await waitFor(() => expect(mockedBuildReportPdf).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('status')).toHaveTextContent('已导出 PDF');
    expect(mockedGetShareImage).toHaveBeenCalledWith(17);
  });

  it('reports a PDF failure without falling back to a silent download', async () => {
    mockedGetShareImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    mockedBuildReportPdf.mockRejectedValue(new Error('canvas unavailable'));

    render(<ShareReportButton recordId={17} reportTitle="中钨高新-000657" reportLanguage="zh" />);
    openMenu();
    fireEvent.click(menuItem('导出 PDF'));

    expect(await screen.findByRole('alert')).toHaveTextContent('PDF 导出失败');
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  });

  it('downloads the generated PNG when native file sharing is unavailable', async () => {
    mockedGetShareImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));

    render(<ShareReportButton recordId={17} reportTitle="中钨高新-000657" reportLanguage="zh" />);
    openMenu();
    fireEvent.click(menuItem('分享图片'));

    await waitFor(() => expect(mockedGetShareImage).toHaveBeenCalledWith(17));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled());
    expect(await screen.findByRole('status')).toHaveTextContent('已生成');
  });

  it('prepares the PNG on the first click and invokes native sharing synchronously on the second click', async () => {
    const nativeShare = vi.fn().mockResolvedValue(undefined);
    let resolveImage: ((blob: Blob) => void) | undefined;
    mockedGetShareImage.mockReturnValue(new Promise((resolve) => {
      resolveImage = resolve;
    }));
    Object.defineProperty(navigator, 'share', { configurable: true, value: nativeShare });
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => true });

    render(<ShareReportButton recordId={18} reportTitle="A股市场复盘" reportLanguage="zh" />);
    openMenu();

    expect(nativeShare).not.toHaveBeenCalled();
    fireEvent.click(menuItem('分享图片'));
    expect(mockedGetShareImage).toHaveBeenCalledWith(18);

    await act(async () => {
      resolveImage?.(new Blob(['png'], { type: 'image/png' }));
    });

    expect(menuItem('再次点击分享')).toBeEnabled();
    expect(nativeShare).not.toHaveBeenCalled();

    fireEvent.click(menuItem('再次点击分享'));
    await waitFor(() => expect(nativeShare).toHaveBeenCalledTimes(1));
    const sharePayload = nativeShare.mock.calls[0][0];
    expect(sharePayload.title).toBe('A股市场复盘');
    expect(sharePayload.files[0].name).toBe('A股市场复盘-18.png');
    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
  });

  it('downloads the PNG when native file sharing rejects', async () => {
    const nativeShare = vi.fn().mockRejectedValue(new Error('activation expired'));
    mockedGetShareImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    Object.defineProperty(navigator, 'share', { configurable: true, value: nativeShare });
    Object.defineProperty(navigator, 'canShare', { configurable: true, value: () => true });

    render(<ShareReportButton recordId={20} reportTitle="A股市场复盘" reportLanguage="zh" />);
    openMenu();
    fireEvent.click(menuItem('分享图片'));

    expect(await screen.findByRole('menuitem', { name: '再次点击分享' })).toBeEnabled();
    expect(nativeShare).not.toHaveBeenCalled();
    expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();

    fireEvent.click(menuItem('再次点击分享'));
    await waitFor(() => expect(nativeShare).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('status')).toHaveTextContent('已生成');
  });

  it('shows the server-provided reason when the renderer is unavailable', async () => {
    mockedGetShareImage.mockRejectedValue({
      response: {
        status: 503,
        data: {
          detail: {
            error: 'share_image_unavailable',
            message: '分享图片生成失败：当前转图引擎不可用。',
          },
        },
      },
    });

    render(<ShareReportButton recordId={21} reportTitle="中钨高新" reportLanguage="zh" />);
    openMenu();
    fireEvent.click(menuItem('分享图片'));

    expect(await screen.findByRole('alert')).toHaveTextContent('当前转图引擎不可用');
    // 复制链接始终可用，避免转图引擎故障时分享入口完全失效。
    expect(menuItem('复制链接')).toBeEnabled();
  });

  it('keeps the share entry hidden for an older desktop bridge without image rendering', () => {
    Object.defineProperty(window, 'dsaDesktop', {
      configurable: true,
      value: { version: '1.0.0' },
    });

    render(<ShareReportButton recordId={23} reportTitle="桌面端报告" reportLanguage="zh" />);

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(mockedGetShareImage).not.toHaveBeenCalled();
  });

  it('renders and downloads share images through the desktop bridge', async () => {
    const renderShareImage = vi.fn().mockResolvedValue(
      new TextEncoder().encode('png').buffer,
    );
    Object.defineProperty(window, 'dsaDesktop', {
      configurable: true,
      value: { version: '3.30.0', renderShareImage },
    });

    render(<ShareReportButton recordId={24} reportTitle="桌面端报告" reportLanguage="zh" />);
    openMenu();
    fireEvent.click(menuItem('分享图片'));

    await waitFor(() => expect(renderShareImage).toHaveBeenCalledWith(24));
    await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled());
    expect(mockedGetShareImage).not.toHaveBeenCalled();
  });

  it('drops cached state and feedback when switching to another record', async () => {
    mockedGetShareImage.mockResolvedValue(new Blob(['a'], { type: 'image/png' }));

    const { rerender } = render(
      <ShareReportButton recordId={21} reportTitle="报告A" reportLanguage="zh" />,
    );

    openMenu();
    fireEvent.click(menuItem('分享图片'));
    expect(await screen.findByRole('status')).toHaveTextContent('已生成');

    rerender(<ShareReportButton recordId={22} reportTitle="报告B" reportLanguage="zh" />);

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '分享报告' })).toBeEnabled();
  });

  it('closes the menu when clicking outside or pressing Escape', () => {
    render(<ShareReportButton recordId={17} reportTitle="中钨高新" reportLanguage="zh" />);

    openMenu();
    expect(screen.getByRole('menu')).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();

    openMenu();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '分享报告' })).toHaveFocus();
  });

  it('moves focus between menu items with the arrow keys', () => {
    render(<ShareReportButton recordId={17} reportTitle="中钨高新" reportLanguage="zh" />);

    openMenu();
    expect(menuItem('复制链接')).toHaveFocus();

    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
    expect(menuItem('导出 PDF')).toHaveFocus();

    fireEvent.keyDown(screen.getByRole('menu'), { key: 'End' });
    expect(menuItem('分享图片')).toHaveFocus();

    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
    expect(menuItem('复制链接')).toHaveFocus();
  });
});
