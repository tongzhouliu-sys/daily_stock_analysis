import { useMemo, useState, type ReactNode } from 'react';
import Markdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { List, ChevronDown, ChevronRight } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { AnswerMeta } from '../../api/agentIntake';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface ChatAnswerProps {
  content: string;
  className?: string;
  answerMeta?: AnswerMeta | null;
  onFollowUp?: (question: string) => void;
  isStreaming?: boolean;
}

interface Heading { text: string; id: string; level: number }

const HEADING_RE = /^(#{2,3})\s+(.+?)\s*#*\s*$/;
const FOLLOWUP_HEADING_RE = /^#{1,4}\s*(?:后续建议|相关追问|接下来可以问|延伸问题|Follow[- ]?up|Related questions)/i;
const LONG_ANSWER_CHARS = 1200;

function plainText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(plainText).join('');
  if (node && typeof node === 'object' && 'props' in node) {
    return plainText((node as { props: { children?: ReactNode } }).props.children);
  }
  return '';
}

function parseHeadings(content: string): Heading[] {
  const used = new Map<string, number>();
  const result: Heading[] = [];
  let inFence = false;
  for (const line of content.split('\n')) {
    if (line.trim().startsWith('```')) { inFence = !inFence; continue; }
    if (inFence) continue;
    const match = HEADING_RE.exec(line.trim());
    if (!match) continue;
    const text = match[2].replace(/[*_`]/g, '').trim();
    const base = `chat-answer-${text.replace(/[^\w\u4e00-\u9fff]+/g, '-').replace(/^-|-$/g, '') || 'section'}`;
    const seen = used.get(base) ?? 0;
    used.set(base, seen + 1);
    result.push({ text, id: seen ? `${base}-${seen}` : base, level: match[1].length });
  }
  return result;
}

/** Follow-up buttons are additive: the Markdown is never split or hidden. */
function parseFollowups(content: string): string[] {
  const lines = content.split('\n');
  let start = -1;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (FOLLOWUP_HEADING_RE.test(lines[i].trim())) { start = i; break; }
  }
  if (start < 0) return [];
  const items: string[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^#{1,4}\s/.test(line.trim())) break;
    const match = /^\s*(?:[-*+]|\d+[.)])\s+(.+)$/.exec(line);
    if (!match) continue;
    const text = match[1]
      .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
      .replace(/[*_`]/g, '')
      .trim();
    if (text && text.length <= 80) items.push(text);
    if (items.length >= 3) break;
  }
  return items;
}

export default function ChatAnswer({
  content, className, answerMeta, onFollowUp, isStreaming = false,
}: ChatAnswerProps) {
  const { t } = useUiLanguage();
  const [collapsed, setCollapsed] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const headings = useMemo(() => parseHeadings(content), [content]);
  const followups = useMemo(() => parseFollowups(content), [content]);
  const headingIds = useMemo(() => {
    const map = new Map<string, string>();
    for (const heading of headings) if (!map.has(heading.text)) map.set(heading.text, heading.id);
    return map;
  }, [headings]);
  const showToc = !isStreaming && headings.length >= 3 && content.length >= LONG_ANSWER_CHARS;
  const canCollapse = !isStreaming && content.length >= LONG_ANSWER_CHARS && headings.length >= 3;
  const visibleFollowups = isStreaming ? [] : followups;

  const components: Components = useMemo(() => ({
    h2: ({ children }) => <h2 id={headingIds.get(plainText(children))}>{children}</h2>,
    h3: ({ children }) => <h3 id={headingIds.get(plainText(children))}>{children}</h3>,
    table: ({ children }) => (
      <div className="chat-answer-table-scroll"><table>{children}</table></div>
    ),
    a: ({ href, children }) => {
      const safe = href && /^https?:\/\//i.test(href) ? href : undefined;
      return (
        <a href={safe} target={safe ? '_blank' : undefined}
          rel={safe ? 'noopener noreferrer' : undefined}>{children}</a>
      );
    },
  }), [headingIds]);

  const sources = (answerMeta?.used_source_refs?.length
    ? answerMeta.used_source_refs : answerMeta?.source_refs ?? [])
    .filter((ref) => typeof ref === 'string' && ref.trim())
    .slice(0, 8);

  return (
    <div className={cn('chat-answer', className)}>
      {showToc && (
        <nav className="chat-answer-toc" aria-label={t('chat.answer.toc')}>
          <span className="chat-answer-toc-label">
            <List size={13} aria-hidden="true" /> {t('chat.answer.toc')}
          </span>
          <ul>
            {headings.map((heading) => (
              <li key={heading.id} className={heading.level === 3 ? 'is-nested' : undefined}>
                <a href={`#${heading.id}`}>{heading.text}</a>
              </li>
            ))}
          </ul>
        </nav>
      )}

      {!isStreaming && answerMeta?.editor_status === 'fallback' && (
        <p className="chat-answer-notice" role="status">{t('chat.answer.preservedOriginal')}</p>
      )}

      <div className={cn('chat-answer-body', collapsed && 'is-collapsed')}>
        <Markdown remarkPlugins={[remarkGfm]} components={components}>{content}</Markdown>
      </div>

      {canCollapse && (
        <button
          type="button"
          className="chat-answer-collapse-btn"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
        >
          {collapsed
            ? <><ChevronRight size={13} aria-hidden="true" /> {t('chat.answer.expand')}</>
            : <><ChevronDown size={13} aria-hidden="true" /> {t('chat.answer.collapse')}</>}
        </button>
      )}

      {!isStreaming && sources.length > 0 && (
        <div className="chat-answer-sources">
          <button
            type="button"
            className="chat-answer-sources-toggle"
            onClick={() => setSourcesOpen((value) => !value)}
            aria-expanded={sourcesOpen}
          >
            {sourcesOpen
              ? <ChevronDown size={13} aria-hidden="true" />
              : <ChevronRight size={13} aria-hidden="true" />}
            {t('chat.answer.sourcesDetails')} ({sources.length})
          </button>
          {sourcesOpen && (
            <ul className="chat-answer-sources-list">
              {sources.map((ref, index) => <li key={`${ref}-${index}`}>{ref}</li>)}
            </ul>
          )}
        </div>
      )}

      {onFollowUp && visibleFollowups.length > 0 && (
        <div className="chat-answer-followups">
          <span className="chat-answer-followups-label">{t('chat.answer.followUps')}</span>
          <div className="chat-answer-followups-list">
            {visibleFollowups.map((question) => (
              <button
                type="button"
                key={question}
                className="chat-answer-followup-btn"
                onClick={() => onFollowUp(question)}
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
