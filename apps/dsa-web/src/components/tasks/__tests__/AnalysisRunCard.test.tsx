import type React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AnalysisRunCard } from '../AnalysisRunCard';
import type { TaskInfo } from '../../../types/analysis';

const baseTask: TaskInfo = {
  taskId: 'task-1',
  stockCode: '600519',
  stockName: '贵州茅台',
  status: 'processing',
  progress: 58,
  message: '贵州茅台：正在生成报告',
  reportType: 'detailed',
  createdAt: new Date().toISOString(),
  startedAt: new Date().toISOString(),
  stage: 'generating',
};

const renderCard = (overrides: Partial<React.ComponentProps<typeof AnalysisRunCard>> = {}) => {
  const props = {
    task: baseTask,
    activeTasks: [baseTask],
    isConnected: true,
    onCancel: vi.fn(),
    onRetry: vi.fn(),
    onOpenRunFlow: vi.fn(),
    onViewReport: vi.fn(),
    onDismiss: vi.fn(),
    onOpenTasksPanel: vi.fn(),
    ...overrides,
  };
  render(<AnalysisRunCard {...props} />);
  return props;
};

describe('AnalysisRunCard', () => {
  it('shows the current stage, server step and cancel affordance while running', () => {
    renderCard();

    const card = screen.getByTestId('analysis-run-card');
    expect(card).toHaveAttribute('data-state', 'running');
    expect(screen.getByText('正在分析')).toBeInTheDocument();
    expect(screen.getByText('贵州茅台：正在生成报告')).toBeInTheDocument();
    expect(screen.getAllByText('生成报告').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText('取消分析'));
  });

  it('queues tasks show their position instead of a stage strip', () => {
    renderCard({
      task: { ...baseTask, status: 'pending', stage: 'queued', message: '任务已加入队列' },
      queuedPosition: 2,
    });

    expect(screen.getByTestId('analysis-run-card')).toHaveAttribute('data-state', 'queued');
    expect(screen.getByText('排队中 · 前面还有 2 个')).toBeInTheDocument();
  });

  it('keeps failures visible with retry and dismiss actions', () => {
    const props = renderCard({
      task: { ...baseTask, status: 'failed', error: '数据源全部不可用' },
    });

    expect(screen.getByTestId('analysis-run-card')).toHaveAttribute('data-state', 'failed');
    expect(screen.getByText('数据源全部不可用')).toBeInTheDocument();

    fireEvent.click(screen.getByText('重试'));
    expect(props.onRetry).toHaveBeenCalledWith(expect.objectContaining({ taskId: 'task-1' }));

    fireEvent.click(screen.getByText('关闭'));
    expect(props.onDismiss).toHaveBeenCalled();
  });

  it('offers a report link after completion and counts other running tasks', () => {
    const other: TaskInfo = { ...baseTask, taskId: 'task-2', stockCode: '000001' };
    renderCard({
      task: { ...baseTask, status: 'completed', progress: 100 },
      activeTasks: [baseTask, other],
    });

    expect(screen.getByTestId('analysis-run-card')).toHaveAttribute('data-state', 'done');
    expect(screen.getByText('另有 1 个任务进行中')).toBeInTheDocument();
    expect(screen.getByText('查看报告')).toBeInTheDocument();
  });

  it('surfaces a dropped connection', () => {
    renderCard({ isConnected: false });

    expect(screen.getByTestId('analysis-run-card')).toHaveAttribute('data-state', 'disconnected');
    expect(screen.getByText('实时连接已断开，正在重连，进度可能延迟')).toBeInTheDocument();
  });

  it('renders market review as an indeterminate bar without a stage strip', () => {
    renderCard({
      task: {
        ...baseTask,
        stockCode: 'market_review',
        stockName: '大盘复盘',
        reportType: 'market_review',
        stage: undefined,
        region: 'cn',
      },
    });

    expect(screen.getByText('正在生成大盘复盘')).toBeInTheDocument();
    expect(screen.getAllByTestId('run-stage-indeterminate').length).toBeGreaterThan(0);
  });
});
