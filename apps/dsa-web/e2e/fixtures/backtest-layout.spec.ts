import { expect, test, type Page } from '@playwright/test';

// Mount the real BacktestPage with fixed API payloads. Component, CSS and
// localization stay untouched; only the network data is deterministic.
const items = Array.from({ length: 12 }, (_, index) => ({
  analysis_history_id: 1000 + index,
  code: index % 2 === 0 ? '600519' : 'AAPL',
  stock_name: index % 3 === 0 ? '贵州茅台股份有限公司' : 'Apple Inc.',
  analysis_date: `2026-11-${String(20 - index).padStart(2, '0')}`,
  eval_window_days: 1,
  engine_version: 'v2',
  eval_status: 'completed',
  operation_advice: '继续持有，等待突破确认后再考虑分批加仓，跌破支撑则止损离场',
  action: index % 2 === 0 ? 'buy' : 'sell',
  action_label: index % 2 === 0 ? '买入' : '卖出',
  trend_prediction: '短线震荡偏强，关注成交量能否放大',
  market_phase: 'intraday',
  actual_return_pct: index % 2 === 0 ? 2.34 : -1.12,
  actual_movement: index % 2 === 0 ? 'up' : 'down',
  direction_expected: index % 2 === 0 ? 'long' : 'cash',
  direction_correct: index % 3 !== 0,
  outcome: index % 3 === 0 ? 'neutral' : index % 2 === 0 ? 'win' : 'loss',
}));

const performance = {
  scope: 'overall',
  eval_window_days: 1,
  engine_version: 'v2',
  computed_at: '2026-11-21T15:00:00+08:00',
  total_evaluations: 128,
  completed_count: 96,
  insufficient_count: 32,
  long_count: 60,
  cash_count: 36,
  win_count: 61,
  loss_count: 25,
  neutral_count: 10,
  direction_accuracy_pct: 63.54,
  win_rate_pct: 63.54,
  neutral_rate_pct: 10.42,
  avg_stock_return_pct: 1.28,
  avg_simulated_return_pct: 0.92,
  stop_loss_trigger_rate: 18.75,
  take_profit_trigger_rate: 24.0,
  ambiguous_rate: 2.1,
  avg_days_to_first_hit: 3.4,
  advice_breakdown: {},
  diagnostics: { phase_breakdown: { premarket: 12, intraday: 74, postmarket: 9, unknown: 1 } },
};

async function mountPage(page: Page) {
  await page.route('**/api/v1/backtest/performance*', async (route) => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(performance) });
  });
  await page.route('**/api/v1/backtest/results*', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ total: 128, page: 1, limit: 20, items }),
    });
  });
  await page.route('**/api/v1/backtest/run', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        processed: 128, saved: 96, completed: 96, insufficient: 32, errors: 0,
        applied_eval_window_days: 1, message: '本轮回测完成，已写入 96 条结果。',
      }),
    });
  });
  await page.route((url) => url.pathname === '/src/main.tsx', async (route) => {
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
      import '/src/research.css';
      import BacktestPage from '/src/pages/BacktestPage.tsx';
      import { UiLanguageProvider } from '/src/contexts/UiLanguageContext.tsx';
      ReactDOMClient.createRoot(document.getElementById('root')).render(React.createElement(UiLanguageProvider, null,
        React.createElement('div', {className: 'research-shell'},
          React.createElement('div', {className: 'research-shell-body'},
            React.createElement('aside', {className: 'research-sidebar'}),
            React.createElement('main', null,
              React.createElement(BacktestPage)
            )
          )
        )
      ));
      `,
    });
  });
  await page.goto('/');
  await expect(page.locator('.backtest-performance-card').first()).toBeVisible();
  await expect(page.getByText('600519').first()).toBeVisible();
}

test('backtest results never overlap their text', async ({ page }, testInfo) => {
  await mountPage(page);

  for (const width of [1440, 1280, 1024, 900, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.waitForTimeout(150);

    const layout = await page.evaluate(() => {
      const cardList = document.querySelector('.backtest-card-list');
      const cards = Array.from(document.querySelectorAll('.backtest-result-card'));
      const table = document.querySelector('.backtest-table');
      const rows = Array.from(table?.querySelectorAll('tbody tr') || []);

      let overlappingCells = 0;
      let overflowingCells = 0;
      rows.forEach((row) => {
        const boxes = Array.from(row.querySelectorAll('td')).map((cell) => {
          const box = cell.getBoundingClientRect();
          return { left: box.left, right: box.right, clientWidth: cell.clientWidth, scrollWidth: cell.scrollWidth };
        });
        overflowingCells += boxes.filter((box) => box.scrollWidth > box.clientWidth + 1).length;
        overlappingCells += boxes.filter((box, index) => index > 0 && box.left < boxes[index - 1].right - 0.5).length;
      });

      const head = table?.querySelector('thead')?.getBoundingClientRect();
      const firstRow = rows[0]?.getBoundingClientRect();
      const headerOverlapsRows = head && firstRow ? head.bottom > firstRow.top + 0.5 : false;

      const metricOverlaps = Array.from(document.querySelectorAll('.backtest-metric-row')).filter((row) => {
        const label = row.querySelector('.label')?.getBoundingClientRect();
        const value = row.querySelector('.value')?.getBoundingClientRect();
        return !label || !value || label.right > value.left + 0.5;
      }).length;

      const overflowingCards = cards.filter((card) => card.scrollWidth > card.clientWidth + 1).length;
      const meta = document.querySelector('.backtest-table-toolbar-meta')?.getBoundingClientRect();
      const hint = document.querySelector('.backtest-table-scroll-hint')?.getBoundingClientRect();

      return {
        rowCount: rows.length,
        cardCount: cards.length,
        cardListWidth: cardList?.clientWidth ?? 0,
        overlappingCells,
        overflowingCells,
        overflowingCards,
        headerOverlapsRows,
        metricOverlaps,
        toolbarGap: meta && hint ? Math.max(0, hint.top - meta.bottom) : 0,
        docOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      };
    });

    expect(layout.metricOverlaps, `metric label/value overlap at ${width}px`).toBe(0);
    expect(layout.docOverflow, `document horizontal overflow at ${width}px`).toBe(false);
    expect(layout.overflowingCards, `card overflow at ${width}px`).toBe(0);

    if (width < 900) {
      // Phones and narrow tablets use readable cards instead of a
      // sideways-scrolling table.
      expect(layout.cardCount, `cards at ${width}px`).toBeGreaterThan(0);
      expect(layout.rowCount, `table hidden at ${width}px`).toBe(0);
      expect(layout.toolbarGap, `toolbar gap at ${width}px`).toBeLessThanOrEqual(24);
    } else {
      expect(layout.rowCount).toBeGreaterThan(0);
      expect(layout.cardCount).toBe(0);
      expect(layout.overlappingCells, `overlapping cells at ${width}px`).toBe(0);
      expect(layout.headerOverlapsRows, `header covers rows at ${width}px`).toBe(false);
    }

    const screenshot = testInfo.outputPath(`backtest-${width}.png`);
    await page.screenshot({ path: screenshot, animations: 'disabled', fullPage: true });
    await testInfo.attach(`backtest-${width}`, { path: screenshot, contentType: 'image/png' });
  }
});

test('english labels keep the result table readable', async ({ page }, testInfo) => {
  await page.addInitScript(() => window.localStorage.setItem('dsa.uiLanguage', 'en'));

  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 1000 });
    await mountPage(page);

    const layout = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('.backtest-table tbody tr'));
      let overlappingCells = 0;
      let wrappedBadges = 0;
      rows.forEach((row) => {
        const boxes = Array.from(row.querySelectorAll('td')).map((cell) => {
          const box = cell.getBoundingClientRect();
          return { left: box.left, right: box.right };
        });
        overlappingCells += boxes.filter((box, index) => index > 0 && box.left < boxes[index - 1].right - 0.5).length;
        row.querySelectorAll('td .rounded-xs').forEach((badge) => {
          if (badge.getBoundingClientRect().height > 26) wrappedBadges += 1;
        });
      });
      return {
        rowCount: rows.length,
        overlappingCells,
        wrappedBadges,
        docOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      };
    });

    expect(layout.rowCount).toBeGreaterThan(0);
    expect(layout.overlappingCells, `overlapping cells at ${width}px`).toBe(0);
    expect(layout.wrappedBadges, `wrapped badges at ${width}px`).toBe(0);
    expect(layout.docOverflow, `document overflow at ${width}px`).toBe(false);

    const screenshot = testInfo.outputPath(`backtest-en-${width}.png`);
    await page.screenshot({ path: screenshot, animations: 'disabled', fullPage: true });
    await testInfo.attach(`backtest-en-${width}`, { path: screenshot, contentType: 'image/png' });
  }
});

test('backtest table header stays opaque and pinned while rows scroll', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 800 });
  await mountPage(page);

  const wrapper = page.locator('.backtest-table-wrapper');
  const measurement = await wrapper.evaluate((element) => {
    const headCell = element.querySelector('thead th');
    const style = headCell ? getComputedStyle(headCell) : null;
    return {
      scrollable: element.scrollHeight > element.clientHeight + 1,
      background: style?.backgroundColor ?? '',
    };
  });

  // Sticky only matters when the wrapper actually scrolls; the header must not
  // rely on a translucent backdrop.
  expect(measurement.scrollable).toBe(true);
  expect(measurement.background).not.toMatch(/rgba\([^)]*,\s*0?\.\d+\)/);

  const pinned = await wrapper.evaluate((element) => {
    element.scrollTop = 240;
    const wrapperTop = element.getBoundingClientRect().top;
    const headTop = element.querySelector('thead th')?.getBoundingClientRect().top ?? -1;
    const firstCellLeft = element.querySelector('tbody td')?.getBoundingClientRect().left ?? -1;
    const wrapperLeft = element.getBoundingClientRect().left;
    return { offset: Math.abs(headTop - wrapperTop), firstCellOffset: Math.abs(firstCellLeft - wrapperLeft) };
  });

  expect(pinned.offset).toBeLessThanOrEqual(1);
  expect(pinned.firstCellOffset).toBeLessThanOrEqual(1);

  const screenshot = testInfo.outputPath('backtest-scrolled-1440.png');
  await page.screenshot({ path: screenshot, animations: 'disabled' });
  await testInfo.attach('backtest-scrolled-1440', { path: screenshot, contentType: 'image/png' });
});
