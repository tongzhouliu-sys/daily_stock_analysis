import { expect, test, type Browser, type Page } from '@playwright/test';

/**
 * Phone chat layout fixture.
 *
 * Every API call is intercepted, so the spec needs no backend, no model and no
 * secrets. Only geometry that CSS alone cannot prove is asserted here: hit
 * sizes, overlap, horizontal overflow and touch font sizes. Time labels are
 * relative to "now", so nothing asserts on the rendered timestamp text.
 */

// Single source for the geometry budgets (AGENTS.md: tests must not photograph
// today's pixel values, but these are product requirements from the phone UX
// requirement doc: primary controls 44, secondary 36, inputs 16).
const PRIMARY_TARGET = 44;
const SECONDARY_TARGET = 36;
const TOUCH_INPUT_FONT_SIZE = 16;
const COMPACT_THREAD_SHARE = 0.7;

const SESSION_ID = '11111111-1111-4111-8111-111111111111';
const NOW = Date.parse('2026-11-23T12:00:00Z');
const iso = (minutesAgo: number) => new Date(NOW - minutesAgo * 60_000).toISOString();

const sessions = [
  {
    session_id: SESSION_ID,
    title: '贵州茅台近期走势与缠论买卖点结构分析',
    message_count: 4,
    created_at: iso(120),
    last_active: iso(5),
    is_pinned: true,
  },
  ...Array.from({ length: 11 }, (_, index) => ({
    session_id: `session-${index}`,
    title: ['宁德时代波浪理论怎么看后市', '比亚迪趋势分析', '中芯国际箱体震荡区间'][index % 3],
    message_count: 2 + index,
    created_at: iso(720 * (index + 2)),
    last_active: iso(720 * (index + 2)),
    is_pinned: false,
  })),
];

const longAnswer = [
  '贵州茅台当前处于日线级别的下跌中枢震荡阶段，短期缺乏明确的第三类买点，建议等待结构确认。',
  '',
  '## 一、关键价位',
  '',
  '| 类型 | 价位（元） | 依据 | 距现价 | 强度 | 备注说明 |',
  '| --- | --- | --- | --- | --- | --- |',
  '| 压力位 | 1520.00 | 日线中枢上沿 | +3.2% | 强 | 前期成交密集区，突破需放量 |',
  '| 支撑位 | 1432.50 | 前低与 MA250 | -2.7% | 中 | 跌破则中枢下移 |',
  '| 止损位 | 1405.00 | 30 分钟一买失败 | -4.6% | 强 | 严格执行 |',
  '',
  '## 二、结构',
  '',
  '```',
  'MACD(12,26,9) DIF=-8.4213 DEA=-6.9087 HIST=-3.0252 周期=30min 数据截至 2026-11-20 15:00',
  '```',
  '',
  '数据来源参考 https://data.eastmoney.com/stockdata/600519.html?from=a_very_long_query_parameter_value_that_does_not_break_naturally 仅供研究。',
  '',
  '## 三、后续建议',
  '',
  '- 如果跌破 1432 该怎么处理仓位？',
  '- 用波浪理论再验证一下这个结论',
].join('\n');

const thinkingSteps = [
  { type: 'stage_done', stage: 'understanding', stage_id: 's1', duration: 1.2 },
  {
    type: 'tool_done',
    tool: 'get_daily_history',
    display_label: '获取日线行情',
    call_id: 'c1',
    duration: 2.3,
    outcome: 'success',
    detail: { record_count: 250 },
  },
  {
    type: 'tool_done',
    tool: 'search_news',
    display_label: '检索相关新闻与公告',
    call_id: 'c3',
    duration: 4.1,
    outcome: 'success',
    detail: { record_count: 12, data_source: 'cache' },
  },
];

const sessionDetail = {
  session_id: SESSION_ID,
  messages: [
    { id: 'm1', role: 'user', content: '用缠论分析一下贵州茅台，重点看日线和 30 分钟级别的买卖点', created_at: iso(30) },
    {
      id: 'm2',
      role: 'assistant',
      content: longAnswer,
      created_at: iso(29),
      thinking_steps: thinkingSteps,
      answer_meta: { source_refs: ['东方财富 日线行情 2026-11-20'] },
    },
    { id: 'm3', role: 'user', content: '如果跌破 1432 该怎么处理仓位？', created_at: iso(10) },
    { id: 'm4', role: 'assistant', content: '跌破 1432 且 30 分钟收盘无法收回，按计划减仓一半；跌破 1405 清仓观望。', created_at: iso(9) },
  ],
  session_state: { selected_skill_ids: ['chan_theory'] },
  active_stock_context: { stock_code: '600519', stock_name: '贵州茅台', canonical_id: 'CN:600519', asset_type: 'stock' },
  active_report_context: null,
  session_state_version: 3,
  session_generation: 'gen-1',
  is_running: false,
  running_progress_steps: [],
};

const runningDetail = {
  ...sessionDetail,
  messages: [
    ...sessionDetail.messages,
    { id: 'm5', role: 'user', content: '用波浪理论再验证一下这个结论', created_at: iso(0) },
  ],
  is_running: true,
  running_turn_id: 'turn-1',
  running_started_at: new Date(Date.now() - 42_000).toISOString(),
  running_heartbeat_at: new Date().toISOString(),
  running_progress_steps: [
    { type: 'stage_done', stage: 'understanding', stage_id: 's1', duration: 1.2 },
    {
      type: 'tool_done',
      tool: 'get_daily_history',
      display_label: '获取日线行情',
      call_id: 'c1',
      duration: 2.3,
      outcome: 'success',
      detail: { record_count: 250 },
    },
    { type: 'tool_start', tool: 'get_chip_distribution', display_label: '获取筹码分布与主力成本区间', call_id: 'c4' },
  ],
};

const skills = {
  default_skill_id: 'bull_trend',
  skills: [
    { id: 'bull_trend', name: '多头趋势', description: '识别均线多头排列并给出持有或减仓提示。' },
    { id: 'chan_theory', name: '缠论', description: '用笔、线段、中枢判断买卖点。' },
    { id: 'wave_theory', name: '波浪理论', description: '用推动浪与调整浪判断所处阶段。' },
    { id: 'box_oscillation', name: '箱体震荡', description: '识别箱体上下沿与突破方向。' },
    { id: 'emotion_cycle', name: '情绪周期', description: '按情绪周期阶段选择进攻或防守。' },
    { id: 'ma_golden_cross', name: '均线金叉', description: '短中期均线金叉的确认条件。' },
  ],
};

type MockOptions = { running?: boolean; empty?: boolean };

async function mockChatApi(page: Page, options: MockOptions = {}) {
  await page.route('**/api/**', async (route) => {
    const { pathname } = new URL(route.request().url());
    if (!pathname.startsWith('/api/')) return route.continue();
    if (pathname.endsWith('/auth/status')) {
      return route.fulfill({ json: { authEnabled: true, loggedIn: true, setupState: 'ready' } });
    }
    if (pathname.endsWith('/agent/status')) {
      return route.fulfill({
        json: { backend: 'litellm', available: true, experimental: false, guided_intake: true, smart_intake: true },
      });
    }
    if (pathname.endsWith('/agent/skills')) return route.fulfill({ json: skills });
    if (pathname.endsWith('/agent/chat/sessions')) {
      return route.fulfill({ json: { sessions: options.empty ? [] : sessions } });
    }
    if (pathname.includes('/agent/chat/sessions/')) {
      const target = pathname.split('/').pop();
      if (options.empty) {
        return route.fulfill({
          json: {
            ...sessionDetail,
            session_id: target,
            messages: [],
            session_state: { selected_skill_ids: null },
            active_stock_context: null,
            session_state_version: 0,
            session_generation: null,
          },
        });
      }
      return route.fulfill({ json: { ...(options.running ? runningDetail : sessionDetail), session_id: target } });
    }
    if (pathname.endsWith('/stocks/watchlist')) return route.fulfill({ json: { stock_codes: [] } });
    if (pathname === '/api/v1/system/config') {
      return route.fulfill({ json: { configVersion: 'fixture', maskToken: '******', items: [] } });
    }
    return route.fulfill({ json: { items: [], total: 0 } });
  });
}

/**
 * Real phone emulation matters here: `hasTouch` is what makes
 * `(hover: none) and (pointer: coarse)` match, which drives the Enter key role
 * and the 16px input floor.
 */
async function newPhonePage(browser: Browser, width: number, height: number) {
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    locale: 'zh-CN',
  });
  return { context, page: await context.newPage() };
}

async function openChat(page: Page, options: MockOptions = {}) {
  await page.addInitScript((id) => localStorage.setItem('dsa_chat_session_id', id), SESSION_ID);
  await mockChatApi(page, options);
  await page.goto('/chat');
  await expect(page.getByTestId('chat-workspace')).toBeVisible();
  await expect(page.locator('[data-message-id="m4"]')).toBeVisible();
}

const boxOf = async (page: Page, selector: string) => {
  const box = await page.locator(selector).first().boundingBox();
  expect(box, `${selector} should render a box`).not.toBeNull();
  return box!;
};

for (const [name, width, height] of [
  ['iphone14', 390, 844],
  ['iphone-se', 375, 667],
] as const) {
  test(`phone chat keeps history, new-chat and message actions usable (${name})`, async ({ browser }, testInfo) => {
    const { context, page } = await newPhonePage(browser, width, height);
    await openChat(page);

    // History is a labelled control, not a bare glyph next to the nav button.
    const history = page.getByRole('button', { name: '历史对话' });
    await expect(history).toBeVisible();
    await expect(history).toContainText('历史');
    const historyBox = (await history.boundingBox())!;
    expect(historyBox.width).toBeGreaterThanOrEqual(PRIMARY_TARGET);
    expect(historyBox.height).toBeGreaterThanOrEqual(PRIMARY_TARGET);

    // New conversation is reachable without opening the drawer first.
    const newChat = page.getByRole('button', { name: '新对话', exact: true });
    await expect(newChat).toBeVisible();
    const newChatBox = (await newChat.boundingBox())!;
    expect(newChatBox.width).toBeGreaterThanOrEqual(PRIMARY_TARGET);
    expect(newChatBox.height).toBeGreaterThanOrEqual(PRIMARY_TARGET);

    // Controls whose panels cannot exist on a phone must not be shown.
    await expect(page.locator('.chat-outline-toggle')).toBeHidden();
    await expect(page.locator('.chat-topbar-title')).toBeHidden();

    // Per-message actions belong to their own message and never overlap.
    const actions = await page.evaluate(() => {
      const bars = [...document.querySelectorAll<HTMLElement>('.chat-message-actions')];
      const rects = bars.map((bar) => {
        const message = bar.closest<HTMLElement>('[data-message-id]');
        return {
          inner: message ? message.contains(bar) : false,
          bar: bar.getBoundingClientRect().toJSON(),
          message: message?.getBoundingClientRect().toJSON() ?? null,
        };
      });
      const overlaps = (a: DOMRect, b: DOMRect) =>
        a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
      let overlapping = 0;
      for (let i = 0; i < rects.length; i += 1) {
        for (let j = i + 1; j < rects.length; j += 1) {
          if (overlaps(rects[i].bar, rects[j].bar)) overlapping += 1;
        }
      }
      return {
        count: rects.length,
        allInsideMessages: rects.every((item) => item.inner),
        allInsideBounds: rects.every((item) => item.message !== null
          && item.bar.top >= item.message.top - 1
          && item.bar.bottom <= item.message.bottom + 1),
        overlapping,
      };
    });
    expect(actions.count).toBeGreaterThan(0);
    expect(actions.allInsideMessages).toBe(true);
    expect(actions.allInsideBounds).toBe(true);
    expect(actions.overlapping).toBe(0);

    const actionTarget = await boxOf(page, '.chat-action-btn');
    expect(actionTarget.width).toBeGreaterThanOrEqual(SECONDARY_TARGET);
    expect(actionTarget.height).toBeGreaterThanOrEqual(SECONDARY_TARGET);

    // Long links and wide tables scroll inside their own container.
    const overflow = await page.evaluate(() => {
      const viewport = document.querySelector('.chat-thread-viewport');
      const table = document.querySelector('.chat-answer-table-scroll');
      const headerCell = document.querySelector('.chat-answer-body th');
      return {
        threadScrollWidth: viewport?.scrollWidth ?? 0,
        threadClientWidth: viewport?.clientWidth ?? 0,
        tableScrollWidth: table?.scrollWidth ?? 0,
        tableClientWidth: table?.clientWidth ?? 0,
        headerCellWidth: headerCell?.getBoundingClientRect().width ?? 0,
        headerCellHeight: headerCell?.getBoundingClientRect().height ?? 0,
      };
    });
    expect(overflow.threadScrollWidth).toBeLessThanOrEqual(overflow.threadClientWidth + 1);
    expect(overflow.tableScrollWidth).toBeGreaterThan(overflow.tableClientWidth);
    // A squeezed six-column table used to render CJK headers one glyph per line.
    expect(overflow.headerCellWidth).toBeGreaterThanOrEqual(40);
    expect(overflow.headerCellHeight).toBeLessThanOrEqual(72);

    // Touch inputs stay at or above the iOS zoom threshold.
    const composerFontSize = await page.evaluate(() => {
      const input = document.querySelector('.chat-composer-input');
      return input ? Number.parseFloat(getComputedStyle(input).fontSize) : 0;
    });
    expect(composerFontSize).toBeGreaterThanOrEqual(TOUCH_INPUT_FONT_SIZE);

    // The thread keeps the majority of the screen above the composer.
    const share = await page.evaluate(() => {
      const viewport = document.querySelector('.chat-thread-viewport');
      return viewport ? viewport.clientHeight / window.innerHeight : 0;
    });
    expect(share).toBeGreaterThanOrEqual(COMPACT_THREAD_SHARE - 0.1);

    await page.screenshot({ path: testInfo.outputPath(`chat-phone-${name}.png`), animations: 'disabled' });

    // History drawer: two-line rows, search, Escape closes and returns focus.
    await history.click();
    const search = page.getByRole('searchbox', { name: '搜索标题' });
    await expect(search).toBeVisible();
    await expect(page.locator('.chat-session-meta').first()).not.toBeEmpty();
    const rowBox = (await page.locator('.chat-session').first().boundingBox())!;
    expect(rowBox.height).toBeGreaterThanOrEqual(52);
    const rowMenuBox = (await page.locator('.chat-session-more').first().boundingBox())!;
    expect(rowMenuBox.width).toBeGreaterThanOrEqual(SECONDARY_TARGET);
    expect(rowMenuBox.height).toBeGreaterThanOrEqual(SECONDARY_TARGET);

    await search.fill('比亚迪');
    await expect(page.locator('.chat-session-meta')).toHaveCount(4);
    await page.screenshot({ path: testInfo.outputPath(`chat-history-search-${name}.png`), animations: 'disabled' });

    await page.keyboard.press('Escape');
    await expect(search).toHaveCount(0);
    await expect(history).toBeFocused();

    // The actions sheet replaces the scroll-closing popover.
    await page.getByRole('button', { name: /置顶、编辑或删除当前对话/ }).click();
    const sheet = page.getByTestId('chat-session-actions');
    await expect(sheet).toBeVisible();
    const sheetItem = (await sheet.getByRole('button', { name: '重命名' }).boundingBox())!;
    expect(sheetItem.height).toBeGreaterThanOrEqual(48);
    const sheetCloseBox = (await sheet.getByRole('button', { name: '关闭' }).boundingBox())!;
    expect(sheetCloseBox.width).toBeGreaterThanOrEqual(PRIMARY_TARGET);
    expect(sheetCloseBox.height).toBeGreaterThanOrEqual(PRIMARY_TARGET);
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await context.close();
  });
}

test('phone running turn keeps progress text free of template placeholders', async ({ browser }) => {
  const { context, page } = await newPhonePage(browser, 390, 844);
  await openChat(page, { running: true });

  const progress = page.getByTestId('chat-live-progress');
  await expect(progress).toBeVisible();
  const text = await progress.innerText();
  expect(text).not.toContain('{');
  expect(text).not.toContain('}');

  // One stop action on a phone: the composer one stays reachable.
  const stopButton = page.locator('.chat-stop-btn');
  await expect(stopButton).toBeVisible();
  expect((await stopButton.boundingBox())!.height).toBeGreaterThanOrEqual(36);
  await expect(page.locator('.chat-live-stop')).toBeHidden();
  await context.close();
});

test('phone empty state fits without scrolling and trims the starter list', async ({ browser }) => {
  const { context, page } = await newPhonePage(browser, 375, 667);
  await page.addInitScript(() => localStorage.removeItem('dsa_chat_session_id'));
  await mockChatApi(page, { empty: true });
  await page.goto('/chat');
  await expect(page.getByTestId('chat-welcome')).toBeVisible();

  const hiddenExtras = await page.evaluate(() => {
    const extras = [...document.querySelectorAll<HTMLElement>('.chat-quick-btn.is-extra')];
    return {
      count: extras.length,
      allHidden: extras.length > 0 && extras.every((element) => getComputedStyle(element).display === 'none'),
    };
  });
  expect(hiddenExtras.count).toBeGreaterThan(0);
  expect(hiddenExtras.allHidden).toBe(true);
  await context.close();
});

test('desktop chat keeps its layout and shows the per-message actions', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openChat(page);

  // Desktop keeps the two-part header and the icon-only history control.
  await expect(page.getByRole('heading', { name: '问股' })).toBeVisible();
  await expect(page.getByRole('button', { name: '新对话', exact: true })).toBeHidden();
  await expect(page.locator('.chat-sessions-panel')).toBeVisible();

  // The action row was previously invisible here because the hover hook it
  // relied on no longer exists in the markup.
  const actionOpacity = await page.evaluate(() => {
    const bar = document.querySelector('.chat-message-actions');
    const button = bar?.querySelector('.chat-action-btn');
    return button ? Number(getComputedStyle(button).opacity) : 0;
  });
  expect(actionOpacity).toBeGreaterThan(0.3);

  const actions = await page.evaluate(() => {
    const bars = [...document.querySelectorAll<HTMLElement>('.chat-message-actions')];
    return bars.map((bar) => {
      const message = bar.closest<HTMLElement>('[data-message-id]');
      return message ? message.contains(bar) : false;
    });
  });
  expect(actions.length).toBeGreaterThan(0);
  expect(actions.every(Boolean)).toBe(true);

  // Desktop keeps the Enter-to-send hint and does not force a 16px input.
  const composerFontSize = await page.evaluate(() => {
    const input = document.querySelector('.chat-composer-input');
    return input ? Number.parseFloat(getComputedStyle(input).fontSize) : 0;
  });
  expect(composerFontSize).toBeLessThan(TOUCH_INPUT_FONT_SIZE);

  await page.screenshot({ path: testInfo.outputPath('chat-desktop.png'), animations: 'disabled' });
});
