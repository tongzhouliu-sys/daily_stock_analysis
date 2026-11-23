import { expect, test, type Page } from '@playwright/test';

test.use({ locale: 'zh-CN' });

// Mobile report reading pane: mount the real HomePage with fixed API payloads
// and assert the layout budget that keeps the conclusion in the first screen.
const report = {
  meta: {
    id: 4821,
    query_id: 'q-4821',
    stock_code: '600519',
    stock_name: '贵州茅台',
    report_type: 'detailed',
    report_language: 'zh',
    created_at: '2026-11-23T14:30:00+08:00',
    current_price: 1688.88,
    change_pct: 1.86,
    model_used: 'openai/gpt-4o-mini',
    asset_type: 'stock',
    market_phase_summary: {
      market: 'cn',
      phase: 'postmarket',
      market_local_time: '2026-11-23T15:30:00+08:00',
      session_date: '2026-11-23',
      effective_daily_bar_date: '2026-11-23',
      is_trading_day: true,
      is_market_open_now: false,
      is_partial_bar: false,
      minutes_to_open: null,
      minutes_to_close: null,
      trigger_source: 'api',
      analysis_intent: 'auto',
      warnings: [],
    },
  },
  summary: {
    analysis_summary:
      '贵州茅台今日放量上行，收盘价站上 5 日、10 日与 20 日均线，MACD 金叉延续，成交量较前五日均值放大 23%。批价企稳回升，i茅台投放节奏放缓，渠道情绪修复；北向资金连续三日净流入白酒板块。短线结构偏强，但上方 1730 一带为前期密集成交区，需要量能继续配合才能有效突破。',
    operation_advice: '持仓者继续持有，回踩 1650 附近可小幅加仓；空仓者等待放量突破 1730 后再介入，跌破 1620 止损。',
    action: 'hold',
    action_label: '持有',
    trend_prediction: '短线震荡偏强，关注 1730 压力位与量能变化，中期趋势维持向上。',
    sentiment_score: 74,
  },
  strategy: {
    ideal_buy: '1652.00（回踩 MA10 支撑）',
    secondary_buy: '1608.50（前低密集区）',
    stop_loss: '1592.00',
    take_profit: '1748.00（前高压力位）',
  },
  details: {
    belong_boards: [
      { name: '白酒', type: '行业' },
      { name: '食品饮料', type: '行业' },
      { name: '消费', type: '概念' },
      { name: '沪股通', type: '概念' },
      { name: 'MSCI中国', type: '概念' },
      { name: '融资融券', type: '概念' },
    ],
    sector_rankings: {
      top: [{ name: '白酒', change_pct: 2.31 }],
      bottom: [{ name: '食品饮料', change_pct: -0.42 }],
    },
    concept_rankings: { top: [{ name: '消费', change_pct: 1.42 }], bottom: [] },
    earnings_calendar: {
      next_earnings_date: '2026-12-18',
      days_to_next: 25,
      coverage: 'cn_reservation_disclosure',
      as_of: '2026-11-23',
      events: [
        { report_period: '2026Q4', expected_disclosure_date: '2026-12-18', disclosure_status: 'scheduled' },
        { report_period: '2026Q3', actual_disclosure_date: '2026-10-25', disclosure_status: 'disclosed' },
      ],
    },
    analysis_context_pack_overview: {
      pack_version: 'v3',
      created_at: '2026-11-23T14:20:00+08:00',
      subject: { code: '600519', stock_name: '贵州茅台', market: 'cn' },
      blocks: [
        { key: 'quote', label: '行情', status: 'available', source: 'mock_quote', warnings: [], missing_reasons: [] },
        { key: 'technical', label: '技术', status: 'available', source: 'mock_tech', warnings: [], missing_reasons: [] },
        { key: 'news', label: '新闻', status: 'missing', source: null, warnings: ['news_provider_timeout'], missing_reasons: ['news_context_missing'] },
        { key: 'fundamentals', label: '基本面', status: 'available', source: 'akshare', warnings: [], missing_reasons: [] },
      ],
      counts: { available: 3, missing: 1, not_supported: 0, fallback: 1, stale: 0, estimated: 0, partial: 0, fetch_failed: 0 },
      data_quality: { overall_score: 86, level: 'good', block_scores: {}, limitations: ['news:missing'] },
      warnings: [],
      metadata: { trigger_source: 'manual', news_result_count: 12 },
    },
    raw_result: { summary: { analysisSummary: '...' } },
    context_snapshot: { version: 'v1' },
  },
};

const historyItem = {
  id: 4821,
  query_id: 'q-4821',
  stock_code: '600519',
  stock_name: '贵州茅台',
  sentiment_score: 74,
  operation_advice: '持有',
  action: 'hold',
  action_label: '持有',
  trend_prediction: '短线震荡偏强',
  current_price: 1688.88,
  change_pct: 1.86,
  model_used: 'openai/gpt-4o-mini',
  created_at: '2026-11-23T14:30:00+08:00',
  report_type: 'detailed',
};

async function mountReport(page: Page) {
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

    if (path === '/api/v1/auth/status') {
      return json({ authEnabled: false, loggedIn: true, setupState: 'no_password' });
    }
    if (path === '/api/v1/system/config/setup/status') {
      return json({ isComplete: true, readyForSmoke: true, requiredMissingKeys: [], checks: [] });
    }
    if (path === '/api/v1/history/4821') return json(report);
    if (path === '/api/v1/history/4821/news') return json({ total: 0, items: [] });
    if (path === '/api/v1/history/4821/diagnostics') return json({});
    if (path === '/api/v1/history') {
      return json({ total: 1, page: 1, limit: 20, items: [historyItem] });
    }
    if (path === '/api/v1/analysis/tasks') return json({ items: [], total: 0 });
    if (path === '/api/v1/agent/skills') return json({ skills: [] });
    if (path === '/api/v1/agent/status') return json({});
    if (path === '/api/v1/stocks/watchlist') return json({ codes: [] });
    if (path === '/api/v1/prediction-markets/overview') return json({ enabled: false });
    return json({});
  });

  await page.goto('/?recordId=4821');
  await expect(page.getByText('核心洞察').first()).toBeVisible({ timeout: 15_000 });
}

async function readBudget(page: Page) {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      return { top: Math.round(rect.top + window.scrollY), height: Math.round(rect.height) };
    };

    const smallTargets = Array.from(
      document.querySelectorAll('.research-reading button, .research-reading a[href], .report-toolbar button'),
    )
      .filter((el) => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        if (rect.width === 0 || rect.height === 0 || style.display === 'none') return false;
        return rect.height < 40;
      })
      .map((el) => `${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`);

    const collapsedHeights = Array.from(document.querySelectorAll('.report-collapse'))
      .filter((el) => (el as HTMLElement).offsetParent !== null)
      .map((el) => Math.round(el.getBoundingClientRect().height));

    return {
      commandBar: box('.research-command-bar'),
      toolbar: box('.report-toolbar'),
      insight: box('.report-key-insight'),
      docOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      smallTargets,
      collapsedHeights,
    };
  });
}

test('mobile report keeps the conclusion in the first screen', async ({ page }, testInfo) => {
  await mountReport(page);

  for (const width of [390, 360]) {
    await page.setViewportSize({ width, height: 844 });
    await page.waitForTimeout(250);

    const budget = await readBudget(page);

    expect(budget.docOverflow, `horizontal overflow at ${width}px`).toBe(false);
    expect(budget.smallTargets, `sub-40px tap targets at ${width}px`).toEqual([]);
    // Composer collapses to a summary row and the toolbar stays single-line.
    expect(budget.commandBar!.height, `composer height at ${width}px`).toBeLessThanOrEqual(70);
    expect(budget.toolbar!.height, `toolbar height at ${width}px`).toBeLessThanOrEqual(90);
    // The core conclusion must start inside the first screen.
    expect(budget.insight!.top, `insight offset at ${width}px`).toBeLessThanOrEqual(560);
    // Two collapsed evidence rows stay compact.
    expect(
      budget.collapsedHeights.reduce((total, height) => total + height, 0),
      `collapsed evidence height at ${width}px`,
    ).toBeLessThanOrEqual(140);

    const screenshot = testInfo.outputPath(`mobile-report-${width}.png`);
    await page.screenshot({ path: screenshot, animations: 'disabled' });
    await testInfo.attach(`mobile-report-${width}`, { path: screenshot, contentType: 'image/png' });
  }
});

test('mobile composer expands on demand and collapses again', async ({ page }) => {
  await mountReport(page);
  await page.setViewportSize({ width: 390, height: 844 });

  const toggle = page.getByRole('button', { name: '新分析' });
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.getByRole('button', { name: '收起' })).toBeVisible();
  await expect(page.getByPlaceholder(/输入股票代码或名称/)).toBeVisible();

  await page.getByRole('button', { name: '收起' }).click();
  await expect(page.getByRole('button', { name: '新分析' })).toBeVisible();
});

test('mobile toolbar reveals secondary actions without a second permanent row', async ({ page }) => {
  await mountReport(page);
  await page.setViewportSize({ width: 360, height: 780 });

  const toolbarBefore = await page.locator('.report-toolbar').boundingBox();
  await page.getByRole('button', { name: '更多操作' }).click();
  await expect(page.getByRole('button', { name: '重新分析' })).toBeVisible();
  const toolbarAfter = await page.locator('.report-toolbar').boundingBox();

  expect(toolbarBefore!.height).toBeLessThanOrEqual(56);
  expect(toolbarAfter!.height).toBeGreaterThan(toolbarBefore!.height);
});
