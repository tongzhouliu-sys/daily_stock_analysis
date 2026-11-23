import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReportMarkdownBody } from '../ReportMarkdownBody';

describe('ReportMarkdownBody', () => {
  it('renders the report footer as one compact line', () => {
    render(
      <ReportMarkdownBody
        content={'# 核心结论\n\n回调买入。\n\n---\n\n*报告生成时间: 2026-11-23 10:00:00*\n\n*本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。*\n'}
      />,
    );

    expect(screen.getByText('回调买入。')).toBeInTheDocument();
    const footer = screen.getByTestId('report-markdown-footer');
    expect(footer.textContent).toContain('报告生成时间：2026-11-23 10:00:00');
    expect(footer.textContent).toContain('本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。');
    expect(footer.className).toContain('text-[11px]');
    expect(footer.querySelector('.whitespace-nowrap')).not.toBeNull();
    expect(screen.queryByText('*报告生成时间: 2026-11-23 10:00:00*')).not.toBeInTheDocument();
  });

  it('keeps content unchanged when there is no footer', () => {
    render(<ReportMarkdownBody content={'# 标题\n\n正文内容。'} />);
    expect(screen.getByText('正文内容。')).toBeInTheDocument();
    expect(screen.queryByTestId('report-markdown-footer')).not.toBeInTheDocument();
  });
});
