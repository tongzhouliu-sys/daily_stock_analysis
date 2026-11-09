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

export const ReportMarkdownBody: React.FC<ReportMarkdownBodyProps> = ({
  content,
  className = '',
  testId,
}) => (
  <div
    data-testid={testId}
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
      {content}
    </Markdown>
  </div>
);
