import type React from 'react';
import { useState, useEffect, useCallback } from 'react';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { ApiErrorAlert, Card } from '../common';
import { DashboardPanelHeader, DashboardStateBlock } from '../dashboard';
import { historyApi } from '../../api/history';
import type { NewsIntelItem, ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportNewsProps {
  recordId?: number;  // 分析历史记录主键 ID
  limit?: number;
  language?: ReportLanguage;
}

const NEWS_SOURCE_TEXT = {
  zh: {
    sourceLabel: '相关资讯/后续检索',
    sourceHint: '来源：报告页补充资讯；是否用于分析以输入数据块为准。',
  },
  en: {
    sourceLabel: 'Related news / follow-up retrieval',
    sourceHint: 'Source: supplemental report-page news; analysis input is shown in Input Blocks.',
  },
  ko: {
    sourceLabel: '관련 뉴스 / 후속 검색',
    sourceHint: '출처: 리포트 페이지 보충 뉴스이며, 분석 사용 여부는 입력 데이터 블록 기준입니다.',
  },
} as const;

const getSourceHost = (url?: string): string | null => {
  if (!url) {
    return null;
  }
  try {
    return new URL(url).hostname.replace(/^www\./i, '');
  } catch {
    return null;
  }
};

/**
 * 资讯区组件 - 终端风格
 */
export const ReportNews: React.FC<ReportNewsProps> = ({ recordId, limit = 8, language = 'zh' }) => {
  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);
  const sourceText = NEWS_SOURCE_TEXT[reportLanguage];
  const [isLoading, setIsLoading] = useState(false);
  const [items, setItems] = useState<NewsIntelItem[]>([]);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const fetchNews = useCallback(async () => {
    if (!recordId) return;
    setIsLoading(true);
    setError(null);

    try {
      const response = await historyApi.getNews(recordId, limit);
      setItems(response.items || []);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [recordId, limit]);

  useEffect(() => {
    setItems([]);
    setError(null);

    if (recordId) {
      fetchNews();
    }
  }, [recordId, fetchNews]);

  if (!recordId) {
    return null;
  }

  return (
    <Card variant="bordered" padding="md" className="home-panel-card report-news">
      <DashboardPanelHeader
        eyebrow={text.newsFeed}
        title={text.relatedNews}
        className="report-section-head mb-2"
        titleClassName="report-section-title"
        actions={(
          <div className="flex items-center gap-2">
            {isLoading ? (
              <div className="home-spinner h-3.5 w-3.5 animate-spin border-2" aria-hidden="true" />
            ) : null}
            <span className="home-accent-chip px-2 py-0.5 text-xs text-muted-text">
              {sourceText.sourceLabel}
            </span>
            <button
              type="button"
              onClick={() => void fetchNews()}
              className="home-accent-link text-xs"
              aria-label={text.refresh}
            >
              {text.refresh}
            </button>
          </div>
        )}
      />
      <p className="report-news-hint">
        {sourceText.sourceHint}
      </p>

      {error && !isLoading && (
        <ApiErrorAlert
          error={error}
          actionLabel={text.retry}
          onAction={() => void fetchNews()}
          dismissLabel={text.dismiss}
        />
      )}

      {isLoading && !error && (
        <DashboardStateBlock
          compact
          loading
          title={text.loadingNews}
        />
      )}

      {!isLoading && !error && items.length === 0 && (
        <DashboardStateBlock
          compact
          title={text.noNews}
          description={text.noNewsDescription}
          icon={(
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 14l-7-7m0 0l-7 7m7-7v18" />
            </svg>
          )}
        />
      )}

      {!isLoading && !error && items.length > 0 && (
        <div className="report-news-list">
          {items.map((item, index) => {
            const sourceHost = getSourceHost(item.url);
            return (
              <article
                key={`${item.title}-${index}`}
                className="home-subpanel report-news-item group"
              >
                <span className="report-news-index font-mono" aria-hidden="true">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <div className="report-news-main">
                  <p className="report-news-title">{item.title}</p>
                  {item.snippet && (
                    <p className="report-news-snippet">{item.snippet}</p>
                  )}
                  {sourceHost ? (
                    <span className="report-news-source font-mono">{sourceHost}</span>
                  ) : null}
                </div>
                {item.url && (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="report-news-link shrink-0"
                    aria-label={text.openLink}
                  >
                    {text.openLink}
                    <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M14 3h7m0 0v7m0-7L10 14"
                      />
                    </svg>
                  </a>
                )}
              </article>
            );
          })}
        </div>
      )}
    </Card>
  );
};
