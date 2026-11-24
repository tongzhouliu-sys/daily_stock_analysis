import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportMarkdownBody } from '../ReportMarkdownBody';

describe('ReportMarkdownBody', () => {
  it('drops the footer time when the report already shows it near the title', () => {
    render(
      <ReportMarkdownBody
        content={'# 核心结论\n\n> 分析日期: **2026-11-23** | 报告生成时间: 10:00:00\n\n回调买入。\n\n---\n\n*报告生成时间: 2026-11-23 10:00:00*\n\n*内容由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。*\n'}
      />,
    );

    expect(screen.getByText('回调买入。')).toBeInTheDocument();
    expect(screen.queryByText('*报告生成时间: 2026-11-23 10:00:00*')).not.toBeInTheDocument();
    const footer = screen.getByTestId('report-markdown-footer');
    expect(footer.textContent).not.toContain('报告生成时间');
    expect(footer.textContent).toContain('内容由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。');
    expect(footer.className).toContain('text-[11px]');
  });

  it('keeps the footer time when the body has no other timestamp', () => {
    render(
      <ReportMarkdownBody
        content={'# 核心结论\n\n回调买入。\n\n---\n\n*报告生成时间: 2026-11-23 10:00:00*\n\n*本报告不构成投资建议。*\n'}
      />,
    );

    const footer = screen.getByTestId('report-markdown-footer');
    expect(footer.textContent).toContain('报告生成时间：2026-11-23 10:00:00');
    expect(footer.textContent).toContain('本报告不构成投资建议。');
  });

  it('keeps content unchanged when there is no footer', () => {
    render(<ReportMarkdownBody content={'# 标题\n\n正文内容。'} />);
    expect(screen.getByText('正文内容。')).toBeInTheDocument();
    expect(screen.queryByTestId('report-markdown-footer')).not.toBeInTheDocument();
  });
});
