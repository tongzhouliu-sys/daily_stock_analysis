import React from 'react';
import type { AnalysisResult, AnalysisReport } from '../../types/analysis';
import { ReportOverview } from './ReportOverview';
import { ReportNews } from './ReportNews';
import { PredictionMarketsPanel } from './PredictionMarketsPanel';
import { PredictionMarketOverviewPanel } from './PredictionMarketOverviewPanel';
import { ReportDetails } from './ReportDetails';
import { ReportDiagnostics } from './ReportDiagnostics';
import { AnalysisContextSummary } from './AnalysisContextSummary';
import { MarketReviewReportView } from './MarketReviewReportView';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportSummaryProps {
  data: AnalysisResult | AnalysisReport;
  isHistory?: boolean;
  /** 自选相关 */
  watchlist?: {
    isInWatchlist: (code: string) => boolean;
    onToggle: (code: string) => void;
    isActioning: boolean;
    actionMessage: string | null;
  };
  onOpenRunFlow?: (recordId: number) => void;
}

/**
 * 完整报告展示组件
 * 按主体内容优先、透明度信息后置的顺序展示报告。
 *
 * 版式：概览封面（含结论与点位）→ 资讯 → 结构化证据 → 数据追溯。
 * 证据类卡片统一单列，展开后才有足够宽度承载块状态、链路诊断与原始 JSON。
 */
export const ReportSummary: React.FC<ReportSummaryProps> = ({
  data,
  isHistory = false,
  watchlist,
  onOpenRunFlow,
}) => {
  // 兼容 AnalysisResult 和 AnalysisReport 两种数据格式
  const report: AnalysisReport = 'report' in data ? data.report : data;
  // 使用 report id，因为 queryId 在批量分析时可能重复，且历史报告详情接口需要 recordId 来获取关联资讯和详情数据
  const recordId = report.meta.id;
  const diagnosticSummary = 'diagnosticSummary' in data ? data.diagnosticSummary : undefined;

  const { meta, summary, strategy, details } = report;
  const reportLanguage = normalizeReportLanguage(meta.reportLanguage);
  const text = getReportText(reportLanguage);
  const modelUsed = (meta.modelUsed || '').trim();
  const shouldShowModel = Boolean(
    modelUsed && !['unknown', 'error', 'none', 'null', 'n/a'].includes(modelUsed.toLowerCase()),
  );

  if (meta.reportType === 'market_review') {
    return (
      <MarketReviewReportView
        report={report}
        recordId={recordId}
        reportLanguage={reportLanguage}
        onOpenRunFlow={onOpenRunFlow}
      />
    );
  }

  return (
    <div className="report-sheet-body animate-fade-in">
      {/* 概览区（首屏）：封面、结论、点位、情绪与财报 */}
      <ReportOverview
        meta={meta}
        summary={summary}
        strategy={strategy}
        details={details}
        isHistory={isHistory}
        watchlist={watchlist}
      />

      {/* 资讯与证据区：统一单列，保证展开后的诊断内容有足够宽度 */}
      <ReportNews recordId={recordId} limit={8} language={reportLanguage} />

      {/* 预测市场结构化证据（LLM 自由文本的权威兜底）*/}
      {/* 预测市场全景（当前池快照） */}
      <PredictionMarketOverviewPanel language={reportLanguage} />

      <PredictionMarketsPanel events={details?.predictionMarkets} language={reportLanguage} />

      {/* 输入数据块低敏摘要 */}
      <AnalysisContextSummary
        overview={details?.analysisContextPackOverview}
        language={reportLanguage}
      />

      {/* 运行诊断摘要 */}
      <ReportDiagnostics
        recordId={recordId}
        summary={diagnosticSummary}
        language={reportLanguage}
        onOpenRunFlow={onOpenRunFlow}
      />

      {/* 透明度与追溯区 */}
      <ReportDetails details={details} recordId={recordId} language={reportLanguage} />

      {/* 分析模型标记（Issue #528）— 报告末尾 */}
      {shouldShowModel && (
        <p className="report-model-note">
          {text.analysisModel}: {modelUsed}
        </p>
      )}
    </div>
  );
};
