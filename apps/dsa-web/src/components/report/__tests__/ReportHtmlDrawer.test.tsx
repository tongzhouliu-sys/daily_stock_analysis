import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { ReportHtmlDrawer } from '../ReportHtmlDrawer';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getReportHtml: vi.fn(),
    ensureReportZhVariant: vi.fn(),
  },
}));

const mockedGetReportHtml = vi.mocked(historyApi.getReportHtml);
const mockedEnsureReportZhVariant = vi.mocked(historyApi.ensureReportZhVariant);

describe('ReportHtmlDrawer', () => {
  beforeEach(() => {
    mockedGetReportHtml.mockReset();
    mockedEnsureReportZhVariant.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the sandbox iframe with HTML from the API', async () => {
    mockedGetReportHtml.mockResolvedValue('<!doctype html><html><body>报告内容</body></html>');
    render(<ReportHtmlDrawer recordId={17} reportTitle="中钨高新" onClose={vi.fn()} />);

    const iframe = await screen.findByTitle('HTML 报告');
    expect(mockedGetReportHtml).toHaveBeenCalledWith(17);
    expect(iframe.getAttribute('sandbox')).toContain('allow-popups');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-scripts');
    expect(iframe.getAttribute('sandbox')).not.toContain('allow-same-origin');
  });

  it('does not leak a previous record response after switching record', async () => {
    let resolveFirst: (value: string) => void = () => undefined;
    const first = new Promise<string>((resolve) => { resolveFirst = resolve; });
    mockedGetReportHtml.mockReturnValueOnce(first).mockResolvedValueOnce('<html>第二份</html>');

    const { rerender } = render(
      <ReportHtmlDrawer recordId={17} reportTitle="报告A" onClose={vi.fn()} />,
    );
    rerender(<ReportHtmlDrawer recordId={18} reportTitle="报告B" onClose={vi.fn()} />);

    await waitFor(() => expect(mockedGetReportHtml).toHaveBeenCalledWith(18));
    const iframe = await screen.findByTitle('HTML 报告');
    expect(iframe.getAttribute('srcdoc')).toContain('第二份');

    // 迟到的旧响应不得覆盖当前记录。
    resolveFirst('<html>第一份（迟到）</html>');
    await waitFor(() => expect(mockedGetReportHtml).toHaveBeenCalledTimes(2));
    expect(screen.getByTitle('HTML 报告').getAttribute('srcdoc')).toContain('第二份');
  });

  it('offers to generate the Chinese variant when the API returns 409', async () => {
    mockedGetReportHtml
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: { detail: { error: 'zh_variant_missing', message: '缺少中文版本' } },
        },
      })
      .mockResolvedValueOnce('<!doctype html><html><body>中文版本</body></html>');
    mockedEnsureReportZhVariant.mockResolvedValue({ status: 'ready' });

    render(<ReportHtmlDrawer recordId={17} reportTitle="Apple" onClose={vi.fn()} />);

    expect(await screen.findByText('该历史报告尚未生成中文版本')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '生成中文版本' }));

    await waitFor(() => expect(mockedEnsureReportZhVariant).toHaveBeenCalledWith(17));
    const iframe = await screen.findByTitle('HTML 报告');
    expect(iframe.getAttribute('srcdoc')).toContain('中文版本');
  });

  it('shows an error message when loading fails', async () => {
    mockedGetReportHtml.mockRejectedValue(new Error('boom'));
    render(<ReportHtmlDrawer recordId={17} reportTitle="报告" onClose={vi.fn()} />);
    await waitFor(() => expect(screen.queryByText(/boom/)).toBeInTheDocument());
  });
});
