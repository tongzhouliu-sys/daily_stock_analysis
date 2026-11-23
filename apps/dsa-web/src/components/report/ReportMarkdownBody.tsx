import type React from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';

interface ReportMarkdownBodyProps {
  content: string;
  className?: string;
  testId?: string;
}

const markdownComponents: Components = {
  table: ({ node, ...props }) => {
    void node;
    return (
      <div className="home-markdown-table-scroll">
        <table {...props} />
      </div>
    );
  },
};

const FOOTER_TIME_LABELS = ['报告生成时间', '生成时间', 'Report Time', 'Generated At', '생성 시각'];
const FOOTER_DISCLAIMER_HINTS = ['不构成投资建议', 'RHCLOUD', 'investment advice', '투자 권유'];

function stripEmphasis(line: string): string {
  return line.trim().replace(/^\*+|\*+$/g, '').trim();
}

function isFooterTimeLine(text: string): boolean {
  return FOOTER_TIME_LABELS.some((label) => text.startsWith(label));
}

function isFooterDisclaimerLine(text: string): boolean {
  return FOOTER_DISCLAIMER_HINTS.some((hint) => text.includes(hint));
}

interface ReportFooter {
  timeLabel: string;
  timeValue: string;
  disclaimer: string;
}

function splitReportFooter(content: string): { body: string; footer: ReportFooter | null } {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  let end = lines.length;
  while (end > 0 && !lines[end - 1].trim()) {
    end -= 1;
  }

  let start = end;
  let timeLine: string | null = null;
  let disclaimerLine: string | null = null;
  let scanned = 0;

  for (let index = end - 1; index >= 0 && scanned < 3; index -= 1) {
    const raw = lines[index];
    if (!raw.trim()) {
      start = index;
      continue;
    }
    const text = stripEmphasis(raw);
    if (!timeLine && isFooterTimeLine(text)) {
      timeLine = text;
      start = index;
      scanned += 1;
      continue;
    }
    if (!disclaimerLine && isFooterDisclaimerLine(text)) {
      disclaimerLine = text;
      start = index;
      scanned += 1;
      continue;
    }
    break;
  }

  if (!timeLine && !disclaimerLine) {
    return { body: content, footer: null };
  }

  let timeLabel = '';
  let timeValue = '';
  if (timeLine) {
    const separator = timeLine.search(/[:：]/);
    if (separator >= 0) {
      timeLabel = timeLine.slice(0, separator).trim();
      timeValue = timeLine.slice(separator + 1).trim();
    } else {
      timeValue = timeLine;
    }
  }

  const bodyLines = lines.slice(0, start);
  while (bodyLines.length > 0 && !bodyLines[bodyLines.length - 1].trim()) {
    bodyLines.pop();
  }
  if (bodyLines.length > 0 && /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(bodyLines[bodyLines.length - 1])) {
    bodyLines.pop();
    while (bodyLines.length > 0 && !bodyLines[bodyLines.length - 1].trim()) {
      bodyLines.pop();
    }
  }

  return {
    body: bodyLines.join('\n'),
    footer: {
      timeLabel,
      timeValue,
      disclaimer: disclaimerLine || '',
    },
  };
}

export const ReportMarkdownBody: React.FC<ReportMarkdownBodyProps> = ({
  content,
  className = '',
  testId,
}) => {
  const { body, footer } = splitReportFooter(content);

  return (
    <div data-testid={testId}>
      <div
        className={`home-markdown-prose max-w-none
          prose-headings:text-foreground prose-headings:font-semibold
          prose-strong:text-foreground prose-strong:font-semibold
          prose-code:before:content-none prose-code:after:content-none
          prose-a:no-underline hover:prose-a:underline
          whitespace-normal break-words
          ${className}
        `}
      >
        <Markdown remarkPlugins={[remarkGfm, remarkBreaks]} components={markdownComponents}>
          {body}
        </Markdown>
      </div>
      {footer ? (
        <div
          data-testid="report-markdown-footer"
          className="mt-4 overflow-x-auto border-t border-border/60 pt-2 text-[11px] leading-4 text-secondary-text"
        >
          <span className="whitespace-nowrap">
            {footer.timeValue ? (
              <span>
                {footer.timeLabel ? `${footer.timeLabel}：` : ''}
                {footer.timeValue}
              </span>
            ) : null}
            {footer.timeValue && footer.disclaimer ? <span className="mx-1.5">·</span> : null}
            {footer.disclaimer ? <span>{footer.disclaimer}</span> : null}
          </span>
        </div>
      ) : null}
    </div>
  );
};
