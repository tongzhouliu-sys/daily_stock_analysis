import { expect, test, type Page } from '@playwright/test';
import type { AnalysisReport, HistoryItem } from '../../src/types/analysis';

// Mount the real drawer inside the real HomePage reading-pane layout. Only the
// report data is fixed; production component, localization and CSS are unchanged.
const items: HistoryItem[] = Array.from({ length: 6 }, (_, index) => ({
  id: index + 1,
  queryId: `q-${index + 1}`,
  stockCode: '600519',
  stockName: '贵州茅台',
  sentimentScore: 80 - index * 6,
  operationAdvice: '买入',
  action: 'buy',
  actionLabel: '买入',
  trendPrediction: '短线震荡偏强',
  currentPrice: 1688.88,
  changePct: index % 2 === 0 ? 1.23 : -0.87,
  volumeRatio: 1.18,
  turnoverRate: 0.86,
  modelUsed: 'openai/gpt-4o-mini',
  createdAt: `2026-11-${String(15 - index).padStart(2, '0')}T14:30:00+08:00`,
}));

// Legacy rows can carry long free-text advice and model names; they must not
// widen the table beyond the scroll container.
items.push({
  ...items[0],
  id: 99,
  action: null,
  actionLabel: null,
  operationAdvice: '继续持有，等待突破确认后再考虑加仓',
  modelUsed: 'openrouter/anthropic/claude-3.5-sonnet-long-name',
  createdAt: '2026-11-01T09:00:00+08:00',
});

const report: AnalysisReport = {
  meta: {
    id: 1,
    queryId: 'q-1',
    stockCode: '600519',
    stockName: '贵州茅台',
    reportType: 'detailed',
    createdAt: '2026-11-15T14:30:00+08:00',
  },
  summary: {
    analysisSummary: '等待确认',
    operationAdvice: '买入',
    action: 'buy',
    actionLabel: '买入',
    trendPrediction: '震荡',
    sentimentScore: 80,
  },
};

async function mountDrawer(page: Page) {
  await page.route((url) => url.pathname === '/src/main.tsx', async (route) => {
    // Reuse the versioned Vite URLs to share React with the workspace's hooks.
    const entry = await (await route.fetch()).text();
    const react = entry.match(/"([^"]*\/react\.js(?:\?[^"]*)?)"/)?.[1];
    const reactDom = entry.match(/"([^"]*\/react-dom_client\.js(?:\?[^"]*)?)"/)?.[1];
    if (!react || !reactDom) throw new Error('Unable to locate Vite React imports');
    await route.fulfill({
      contentType: 'application/javascript',
      body: `
      import React from ${JSON.stringify(react)};
      import ReactDOMClient from ${JSON.stringify(reactDom)};
      import '/src/index.css';
      import { StockHistoryTrendDrawer } from '/src/components/history/StockHistoryTrendDrawer.tsx';
      import { UiLanguageProvider } from '/src/contexts/UiLanguageContext.tsx';
      const noop = async () => {};
      ReactDOMClient.createRoot(document.getElementById('root')).render(React.createElement(UiLanguageProvider, null,
        React.createElement('div', {className: 'research-shell'},
          React.createElement('div', {className: 'research-shell-body'},
            React.createElement('aside', {className: 'research-sidebar'}),
            React.createElement('main', null,
              React.createElement('div', {className: 'research-home'},
                React.createElement('div', {className: 'research-home-content'},
                  React.createElement('div', {className: 'research-desk'},
                    React.createElement('section', {className: 'research-reading'},
                      React.createElement('div', {className: 'research-panel-heading'}, React.createElement('h2', null, '分析报告')),
                      React.createElement('div', {className: 'research-reading-scroll', id: 'pane'},
                        React.createElement(StockHistoryTrendDrawer, {
                          report: ${JSON.stringify(report)},
                          items: ${JSON.stringify(items)},
                          total: 64,
                          hasMore: true,
                          isLoading: false,
                          isLoadingMore: false,
                          filters: { range: 'all', model: 'all', sort: 'desc' },
                          onClose: noop, onRangeChange: noop, onLoadMore: noop, onSelectRecord: noop, onRetry: noop
                        })
                      )
                    ),
                    React.createElement('aside', {className: 'research-records'})
                  )
                )
              )
            )
          )
        )
      ));
      `,
    });
  });
  await page.goto('/');
  await expect(page.getByText('历史趋势').first()).toBeVisible();
}

test('history trend table never overlaps its cells at narrow reading panes', async ({ page }, testInfo) => {
  await mountDrawer(page);

  for (const width of [1440, 1280, 1024, 900, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(100);

    const layout = await page.evaluate(() => {
      const table = document.querySelector('#pane table');
      const wrapper = table?.parentElement;
      const rows = Array.from(table?.querySelectorAll('tbody tr') || []);
      let overflowingCells = 0;
      let overlappingCells = 0;
      rows.forEach((row) => {
        const boxes = Array.from(row.querySelectorAll('td')).map((cell) => {
          const box = cell.getBoundingClientRect();
          return { left: box.left, right: box.right, clientWidth: cell.clientWidth, scrollWidth: cell.scrollWidth };
        });
        overflowingCells += boxes.filter((box) => box.scrollWidth > box.clientWidth + 1).length;
        overlappingCells += boxes.filter((box, index) => index > 0 && box.left < boxes[index - 1].right - 0.5).length;
      });
      const lastCell = rows[0]?.querySelector('td:last-child');
      return {
        rowCount: rows.length,
        wrapperClientWidth: wrapper?.clientWidth ?? 0,
        wrapperScrollWidth: wrapper?.scrollWidth ?? 0,
        overflowingCells,
        overlappingCells,
        lastCellRight: lastCell?.getBoundingClientRect().right ?? 0,
        wrapperRight: wrapper?.getBoundingClientRect().right ?? 0,
      };
    });

    expect(layout.rowCount).toBeGreaterThan(0);

    expect(layout.overflowingCells, `overflowing cells at ${width}px`).toBe(0);
    expect(layout.overlappingCells, `overlapping cells at ${width}px`).toBe(0);
    if (layout.wrapperScrollWidth <= layout.wrapperClientWidth + 1) {
      expect(layout.lastCellRight).toBeLessThanOrEqual(layout.wrapperRight + 1);
    }

    const screenshot = testInfo.outputPath(`history-trend-${width}.png`);
    await page.screenshot({ path: screenshot, animations: 'disabled' });
    await testInfo.attach(`history-trend-${width}`, { path: screenshot, contentType: 'image/png' });
  }
});

test('history trend advice reveals the full conclusion in a masonry panel', async ({ page }, testInfo) => {
  await mountDrawer(page);

  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(100);

    // The last row carries legacy free-text advice that the table truncates.
    await page.getByTestId('history-advice-trigger').last().hover();

    const panel = page.getByTestId('history-advice-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByText('继续持有，等待突破确认后再考虑加仓').first()).toBeVisible();

    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
    expect(box!.y + box!.height).toBeLessThanOrEqual(1000 + 1);

    const columnCount = await panel.evaluate((element) => {
      const offsets = Array.from(element.querySelectorAll('section')).map((section) =>
        Math.round(section.getBoundingClientRect().left),
      );
      return new Set(offsets).size;
    });
    expect(columnCount).toBe(width >= 768 ? 2 : 1);

    const screenshot = testInfo.outputPath(`history-trend-advice-${width}.png`);
    await page.screenshot({ path: screenshot, animations: 'disabled' });
    await testInfo.attach(`history-trend-advice-${width}`, { path: screenshot, contentType: 'image/png' });

    await page.mouse.move(0, 0);
    await expect(panel).toBeHidden();
  }
});
