import type { Message } from '../stores/agentChatStore';

export type ExportFormat = 'md' | 'pdf' | 'word';

/**
 * Generate a timestamp string for filenames.
 */
function generateTimestamp(): { dateStr: string; timeStr: string } {
  const now = new Date();
  const dateStr = now.toISOString().slice(0, 10).replace(/-/g, '');
  const pad = (n: number) => n.toString().padStart(2, '0');
  const timeStr = pad(now.getHours()) + pad(now.getMinutes());
  return { dateStr, timeStr };
}

/**
 * Trigger browser download of a blob.
 */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Format chat messages as Markdown for export.
 */
export function formatSessionAsMarkdown(messages: Message[]): string {
  const now = new Date();
  const timeStr = now.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  const lines: string[] = [
    '# 问股会话',
    '',
    `生成时间: ${timeStr}`,
    '',
  ];

  for (const msg of messages) {
    const heading = msg.role === 'user' ? '## 用户' : '## AI';
    if (msg.role === 'assistant' && msg.skillName) {
      lines.push(`${heading} (${msg.skillName})`);
    } else {
      lines.push(heading);
    }
    lines.push('');
    lines.push(msg.content);
    lines.push('');
  }

  return lines.join('\n');
}

/**
 * Trigger browser download of session as .md file.
 */
export function downloadSessionAsMarkdown(messages: Message[]): void {
  const content = formatSessionAsMarkdown(messages);
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const { dateStr, timeStr } = generateTimestamp();
  const filename = `问股会话_${dateStr}_${timeStr}.md`;
  downloadBlob(blob, filename);
}

/**
 * Legacy alias for backward compatibility.
 */
export const downloadSession = downloadSessionAsMarkdown;

/**
 * Render markdown content to HTML for PDF/Word export.
 * Uses a simple markdown-to-HTML conversion.
 */
function markdownToHtml(markdown: string): string {
  let html = markdown
    // Escape HTML
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Headers
    .replace(/^### (.*$)/gm, '<h3>$1</h3>')
    .replace(/^## (.*$)/gm, '<h2>$1</h2>')
    .replace(/^# (.*$)/gm, '<h1>$1</h1>')
    // Bold
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    // Italic
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    // Code blocks
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>')
    // Inline code
    .replace(/`(.*?)`/g, '<code>$1</code>')
    // Line breaks
    .replace(/\n/g, '<br>');

  return html;
}

/**
 * Export session as PDF using html2canvas and jsPDF.
 */
export async function downloadSessionAsPdf(messages: Message[]): Promise<void> {
  const [html2canvasModule, jsPDFModule] = await Promise.all([
    import('html2canvas'),
    import('jspdf'),
  ]);

  const html2canvas = html2canvasModule.default;
  const { jsPDF } = jsPDFModule;

  // Create a temporary container for rendering
  const container = document.createElement('div');
  container.style.position = 'absolute';
  container.style.left = '-9999px';
  container.style.top = '0';
  container.style.width = '800px';
  container.style.padding = '40px';
  container.style.background = '#ffffff';
  container.style.color = '#000000';
  container.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

  const now = new Date();
  const timeStr = now.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  let htmlContent = `
    <div style="font-size: 14px; line-height: 1.6;">
      <h1 style="font-size: 24px; margin-bottom: 8px; color: #1a1a1a;">问股会话</h1>
      <p style="font-size: 12px; color: #666; margin-bottom: 24px;">生成时间: ${timeStr}</p>
      <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 20px 0;">
  `;

  for (const msg of messages) {
    const roleLabel = msg.role === 'user' ? '用户' : 'AI';
    const skillLabel = msg.role === 'assistant' && msg.skillName ? ` (${msg.skillName})` : '';
    const bgColor = msg.role === 'user' ? '#f0f0f0' : '#e8f4f8';
    const headerColor = msg.role === 'user' ? '#333' : '#0066cc';

    htmlContent += `
      <div style="margin-bottom: 20px; padding: 16px; background: ${bgColor}; border-radius: 8px;">
        <h2 style="font-size: 16px; color: ${headerColor}; margin: 0 0 12px 0;">${roleLabel}${skillLabel}</h2>
        <div style="font-size: 14px; line-height: 1.6; color: #333;">
          ${markdownToHtml(msg.content)}
        </div>
      </div>
    `;
  }

  htmlContent += '</div>';
  container.innerHTML = htmlContent;
  document.body.appendChild(container);

  try {
    const canvas = await html2canvas(container, {
      scale: 2,
      useCORS: true,
      logging: false,
      backgroundColor: '#ffffff',
    });

    const imgData = canvas.toDataURL('image/png');
    const pdf = new jsPDF('p', 'mm', 'a4');
    const pdfWidth = pdf.internal.pageSize.getWidth();
    const pdfHeight = pdf.internal.pageSize.getHeight();
    const imgWidth = pdfWidth - 20; // 10mm margins
    const imgHeight = (canvas.height * imgWidth) / canvas.width;

    let heightLeft = imgHeight;
    let position = 10;

    pdf.addImage(imgData, 'PNG', 10, position, imgWidth, imgHeight);
    heightLeft -= pdfHeight;

    while (heightLeft > 0) {
      position = heightLeft - imgHeight + 10;
      pdf.addPage();
      pdf.addImage(imgData, 'PNG', 10, position, imgWidth, imgHeight);
      heightLeft -= pdfHeight;
    }

    const { dateStr, timeStr: timeSuffix } = generateTimestamp();
    const filename = `问股会话_${dateStr}_${timeSuffix}.pdf`;
    pdf.save(filename);
  } finally {
    document.body.removeChild(container);
  }
}

/**
 * Export session as Word document using docx library.
 */
export async function downloadSessionAsWord(messages: Message[]): Promise<void> {
  const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } = await import('docx');

  const now = new Date();
  const timeStr = now.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  const children: Paragraph[] = [
    new Paragraph({
      text: '问股会话',
      heading: HeadingLevel.HEADING_1,
      alignment: AlignmentType.LEFT,
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: `生成时间: ${timeStr}`,
          size: 20,
          color: '666666',
        }),
      ],
    }),
    new Paragraph({ text: '' }),
  ];

  for (const msg of messages) {
    const roleLabel = msg.role === 'user' ? '用户' : 'AI';
    const skillLabel = msg.role === 'assistant' && msg.skillName ? ` (${msg.skillName})` : '';

    children.push(
      new Paragraph({
        text: `${roleLabel}${skillLabel}`,
        heading: HeadingLevel.HEADING_2,
      }),
    );

    // Split content by lines and add each as a paragraph
    const lines = msg.content.split('\n');
    for (const line of lines) {
      children.push(
        new Paragraph({
          children: [
            new TextRun({
              text: line,
              size: 22,
            }),
          ],
        }),
      );
    }

    children.push(new Paragraph({ text: '' }));
  }

  const doc = new Document({
    sections: [
      {
        children,
      },
    ],
  });

  const blob = await Packer.toBlob(doc);
  const { dateStr, timeStr: timeSuffix } = generateTimestamp();
  const filename = `问股会话_${dateStr}_${timeSuffix}.docx`;
  downloadBlob(blob, filename);
}

/**
 * Download session in the specified format.
 */
export async function downloadSessionInFormat(messages: Message[], format: ExportFormat): Promise<void> {
  switch (format) {
    case 'md':
      downloadSessionAsMarkdown(messages);
      break;
    case 'pdf':
      await downloadSessionAsPdf(messages);
      break;
    case 'word':
      await downloadSessionAsWord(messages);
      break;
  }
}
