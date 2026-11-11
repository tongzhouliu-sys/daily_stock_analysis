import React, { useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import { cn } from '../../utils/cn';

interface ChatAnswerProps {
  content: string;
  className?: string;
}

/**
 * T5: Structured answer rendering component
 * 
 * Renders analysis answers with:
 * - Conclusion summary (always visible)
 * - Collapsible evidence sections
 * - Risk warnings
 * - Source catalog
 * - Follow-up suggestions
 */
export default function ChatAnswer({ content, className }: ChatAnswerProps) {
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['conclusion']));

  const toggleSection = (section: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(section)) {
        next.delete(section);
      } else {
        next.add(section);
      }
      return next;
    });
  };

  // Parse content into sections
  const sections = parseAnswerSections(content);

  return (
    <div className={cn('chat-answer', className)}>
      {/* Conclusion Summary - Always visible */}
      {sections.conclusion && (
        <div className="chat-answer-section chat-answer-conclusion">
          <div className="chat-answer-section-header">
            <h3 className="chat-answer-section-title">结论摘要</h3>
          </div>
          <div className="chat-answer-section-content">
            <Markdown remarkPlugins={[remarkGfm]}>{sections.conclusion}</Markdown>
          </div>
        </div>
      )}

      {/* Key Arguments - Collapsible */}
      {sections.arguments && (
        <div className="chat-answer-section chat-answer-arguments">
          <button
            type="button"
            className="chat-answer-section-header chat-answer-section-toggle"
            onClick={() => toggleSection('arguments')}
            aria-expanded={expandedSections.has('arguments')}
          >
            <h3 className="chat-answer-section-title">核心论据</h3>
            {expandedSections.has('arguments') ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )}
          </button>
          {expandedSections.has('arguments') && (
            <div className="chat-answer-section-content">
              <Markdown remarkPlugins={[remarkGfm]}>{sections.arguments}</Markdown>
            </div>
          )}
        </div>
      )}

      {/* Risk Factors - Collapsible */}
      {sections.risks && (
        <div className="chat-answer-section chat-answer-risks">
          <button
            type="button"
            className="chat-answer-section-header chat-answer-section-toggle"
            onClick={() => toggleSection('risks')}
            aria-expanded={expandedSections.has('risks')}
          >
            <h3 className="chat-answer-section-title">风险提示</h3>
            {expandedSections.has('risks') ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )}
          </button>
          {expandedSections.has('risks') && (
            <div className="chat-answer-section-content">
              <Markdown remarkPlugins={[remarkGfm]}>{sections.risks}</Markdown>
            </div>
          )}
        </div>
      )}

      {/* Source Catalog - Collapsible */}
      {sections.sources && (
        <div className="chat-answer-section chat-answer-sources">
          <button
            type="button"
            className="chat-answer-section-header chat-answer-section-toggle"
            onClick={() => toggleSection('sources')}
            aria-expanded={expandedSections.has('sources')}
          >
            <h3 className="chat-answer-section-title">数据来源</h3>
            {expandedSections.has('sources') ? (
              <ChevronDown size={16} />
            ) : (
              <ChevronRight size={16} />
            )}
          </button>
          {expandedSections.has('sources') && (
            <div className="chat-answer-section-content">
              <Markdown remarkPlugins={[remarkGfm]}>{sections.sources}</Markdown>
            </div>
          )}
        </div>
      )}

      {/* Follow-up Suggestions - Always visible if present */}
      {sections.followups && (
        <div className="chat-answer-section chat-answer-followups">
          <div className="chat-answer-section-header">
            <h3 className="chat-answer-section-title">后续建议</h3>
          </div>
          <div className="chat-answer-section-content">
            <Markdown remarkPlugins={[remarkGfm]}>{sections.followups}</Markdown>
          </div>
        </div>
      )}

      {/* Fallback: If no sections parsed, render as plain markdown */}
      {!sections.conclusion && !sections.arguments && !sections.risks && (
        <div className="chat-answer-plain">
          <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
        </div>
      )}
    </div>
  );
}

/**
 * Parse markdown content into structured sections
 */
function parseAnswerSections(content: string): {
  conclusion?: string;
  arguments?: string;
  risks?: string;
  sources?: string;
  followups?: string;
} {
  const sections: ReturnType<typeof parseAnswerSections> = {};
  
  // Split by headers
  const lines = content.split('\n');
  let currentSection: keyof typeof sections | null = null;
  let currentContent: string[] = [];

  const flushSection = () => {
    if (currentSection && currentContent.length > 0) {
      sections[currentSection] = currentContent.join('\n').trim();
    }
    currentContent = [];
  };

  for (const line of lines) {
    // Detect section headers
    if (line.match(/^#{1,3}\s*结论摘要/)) {
      flushSection();
      currentSection = 'conclusion';
    } else if (line.match(/^#{1,3}\s*核心论据/)) {
      flushSection();
      currentSection = 'arguments';
    } else if (line.match(/^#{1,3}\s*风险提示/)) {
      flushSection();
      currentSection = 'risks';
    } else if (line.match(/^#{1,3}\s*数据来源/)) {
      flushSection();
      currentSection = 'sources';
    } else if (line.match(/^#{1,3}\s*后续建议/)) {
      flushSection();
      currentSection = 'followups';
    } else if (currentSection) {
      currentContent.push(line);
    } else {
      // Content before any section header - treat as conclusion
      if (!sections.conclusion) {
        currentSection = 'conclusion';
        currentContent.push(line);
      }
    }
  }

  flushSection();
  return sections;
}
