# 开发任务书：回测结果区排版与文字遮挡治理

> 状态：已实施
> 基线：`main` @ `cf4f8428`
> 任务类型：`fix`（含小幅 `refactor`）
> 影响面：`apps/dsa-web/src/pages/BacktestPage.tsx`、`apps/dsa-web/src/index.css`、Web e2e fixture、`docs/CHANGELOG.md`
> 问题入口：回测页首次加载 / 运行回测后有结果时，结果区文字被遮挡、截断或相互挤压

---

## 1. 背景与问题

### 1.1 现象

回测页在「空状态」下排版正常，但一旦加载出结果（整体表现卡 + 结果表 + 分页同时出现），读屏体验明显变差：

- 表格滚动时表头文字与下方行文字叠在一起，表头看起来「糊成一片」。
- 长股票名、长操作建议、长阶段文本把列挤宽；横向滚动后看不到当前行属于哪只股票。
- 窄屏下筛选区/工具栏/分页的文字互相挤压或被裁掉。
- 性能卡在窄侧栏里 label 与 value 贴在一起，长英文标签进一步压到数值上。

### 1.2 现状代码锚点

| 区域 | 锚点 | 现状问题 |
| --- | --- | --- |
| 结果区容器 | `BacktestPage.tsx` 624–647 行 | `overflow-hidden` + `flex-1` 但没有确定高度，形成「伪滚动区」：表格不会在自己的容器里滚动，表头粘性定位也失效 |
| 表格外壳 | `index.css` `.backtest-table-wrapper` | `overflow-x: auto` 同时把 `overflow-y` 计算成 `auto`，成为最近的滚动容器；表头 `sticky` 只相对它生效，结果区实际滚动时表头不跟随 |
| 表头 | `index.css` `.backtest-table-head-cell` | `background: hsl(var(--elevated) / 0.94)` + `backdrop-filter: blur(12px)`，半透明背景下行文字会透出，视觉上就是「字挡住字」 |
| 预测列 | `BacktestPage.tsx` 724–743 行 | `max-w-[220px]` 加在 `td` 上，内层 `truncate` 的父级是 `inline-flex` 且没有宽度约束，`truncate` 实际不生效；长文本撑宽列 |
| 指标行 | `index.css` `.backtest-metric-row` | label 与 value 用 `space-between`，label 无 `min-width: 0`、value 无 `flex-shrink: 0`，窄侧栏里两者可能重叠 |
| 工具栏 | `index.css` `.backtest-table-toolbar` | meta 文案可能很长，与右侧滚动提示在同一行争抢宽度，没有换行/省略策略 |
| 筛选区 | `BacktestPage.tsx` 501–607 行 | 每个控件组 `whitespace-nowrap` + 固定宽度，窄屏只能整体换行，日期与按钮组合容易挤出容器 |
| 分页 | `BacktestPage.tsx` 774–784 行 | 页码按钮与总数文案居中，窄屏下没有收缩策略 |

### 1.3 根因归纳

1. **滚动容器选错层级**：本应在「结果表外壳」内纵向滚动并让表头吸附，现在滚动发生在整页/外层 section，表头要么不吸附，要么吸附在错误的容器上。
2. **半透明 + 模糊表头**：视觉上牺牲了可读性，行内容透过表头，产生文字重叠感。
3. **文本宽度契约缺失**：表格列没有明确的截断/换行规则，`truncate` 落在无宽度约束的 flex 容器里，等于没写。
4. **窄屏布局没有降级策略**：筛选区、工具栏、指标卡都按「桌面宽度充足」假设排版。

### 1.4 目标

- **G1** 结果区在 `lg` 及以上成为高度受控的独立工作区：表头真正吸顶，表格在容器内纵向 + 横向滚动，页面其余部分不跟着漂。
- **G2** 表头完全不透明，滚动时行文字不再透出；表头与首行之间无重叠、无缝隙。
- **G3** 所有单元格遵守「要么换行，要么省略 + Tooltip」的单一规则；任何宽度下单元格不重叠、不越界。
- **G4** 窄屏（≤ 900px）下筛选区、指标卡、工具栏、分页均不出现文字挤压或被裁切。
- **G5** 横向滚动时首列（股票）保持可见，用户始终知道当前行是哪只股票。
- **G6** 不改变任何 API / 数据结构 / 分页 / 回测语义，只动展示层。

### 1.5 非目标

- 不改回测引擎、指标口径、接口字段和分页逻辑。
- 不新增页面、不引入第三方表格/虚拟滚动库。
- 不重做视觉主题；配色沿用现有 design token。
- 不把回测结果导出、图表化等新能力塞进本次任务。

---

## 2. 方案设计

### 2.1 结果区布局

```
research-page (flex column, >=1400px 时锁定视口高度)
├─ PageHeader
├─ 筛选 header（自适应网格：1 / 2 / 3 / 5 列）
└─ 结果工作区
   ├─ ≥1400px：性能侧栏（256px，独立滚动）+ 结果面板（表格吸顶滚动）
   ├─ 900–1399px：性能卡横排在上（指标行分栏），结果表占满宽度
   └─ <900px：性能卡单列 + 结果卡片列表（不再横向滚动表格）
```

- `≥1400px` 才启用「工作区固定高度」：`calc(100dvh - var(--masthead-height))`，用 flex 链约束高度，`min-height` 兜底；不强塞魔法数。
- `900–1399px` 恢复整页滚动，性能卡内部改用网格排布指标，避免占掉半屏高度。
- `<900px` 用卡片列表取代表格，彻底消除横向滚动与列被裁切。

### 2.2 表头与表格

- 表头改为 **不透明** 背景（`hsl(var(--elevated))`），移除 `backdrop-filter`，`z-index: 3`。
- 表头字号 10px、字距 0.04em，允许在词边界换行，避免 `PERFORMANCE` 之类长单词被从中间截断。
- `table-layout: fixed` + `<colgroup>` 明确列宽（最小宽度 768px），所有正文单元格 `overflow: hidden`，文本统一走「换行 / 省略 + Tooltip」。
- 首列（股票）在横向滚动时 `sticky left: 0` 并继承行背景，表头首列 `z-index: 4`。
- 状态/结果徽章内部文本可省略并带悬浮提示，长英文状态不再把徽章撑成两行。

### 2.3 指标卡 / 筛选区 / 工具栏

- 指标行：label `flex: 1 1 auto; min-width: 0;` 允许换行；value `flex: none; tabular-nums;`，两者之间至少 12px 间距。
- 筛选区：`grid` 响应式——手机 1 列、平板 2 列、1024–1279 三列（股票代码占 2 列）、≥1280 自动 5–6 列；输入框占满网格单元，不再依赖 `whitespace-nowrap`。
- 工具栏：meta `min-width: 0` 可换行，滚动提示窄屏单独一行；只在 ≥641px 才允许 meta 伸展，避免列向布局把 `flex-basis` 当成高度。
- 分页：容器可换行，总数文案可换行，不贴边。

### 2.4 窄屏卡片

`<900px` 渲染 `backtest-result-card`：头部为代码 / 名称 + 结果 / 状态徽章，中部 2 列字段（日期、阶段、AI 预测、实际表现、方向匹配），底部操作建议最多 2 行。完整建议仍保留在 DOM 文本中，不使用横向滚动。

### 2.4 可访问性

- 首列吸附不改变 DOM 顺序与语义。
- 所有 `aria-label`、Tooltip 的 `aria-describedby` 保持现状。
- 颜色对比度不降低：表头文字仍用 `secondary-text`，背景改为实色后对比度只会提高。

---

## 3. 验收标准（必须可测量）

在 e2e fixture（真实页面 + mock API，视口 1440/1280/1024/900/768/390，高度 1000）下：

1. 任意视口，`tbody tr` 内相邻 `td` 的包围盒 **不重叠**（`left >= prev.right - 0.5`）；`<900px` 无表格，改查卡片不溢出。
2. 任意视口，`thead` 的 `bottom <= 首行 top + 0.5`（表头不压行）。
3. 1440×800 下表头真正吸顶：滚动 240px 后 `th` 顶边与表格外壳顶边误差 ≤ 1px；首列左边界同样不动。
4. 表头计算样式背景为不透明色（不含 `rgba(...)` 透明度），滚动后行文字不穿透。
5. 指标卡 `.backtest-metric-row` 的 label 右边界始终小于 value 左边界。
6. 页面无横向溢出：`document.documentElement.scrollWidth <= innerWidth + 1`，卡片无内部溢出。
7. 900px / 390px 下工具栏 meta 与滚动提示的垂直间距 ≤ 24px，筛选区、分页文字完整可见。
8. 英文界面（1440 / 1024）下徽章不换行、单元格不重叠、`Completed` 等状态完整显示。
9. 现有 `BacktestPage` 单测、Web lint/build、全部 fixture e2e 通过。

---

## 4. 实施清单

| # | 文件 | 改动 |
| --- | --- | --- |
| 1 | `apps/dsa-web/src/pages/BacktestPage.tsx` | 结果区结构、窄屏卡片、列定义、徽章省略、筛选区网格、指标分块、分页容器 |
| 2 | `apps/dsa-web/src/index.css` | 表头实色 + sticky、首列吸附、卡片列表、指标行/工具栏/表格单元格宽度契约、响应式断点 |
| 3 | `apps/dsa-web/src/locales/featureText.ts` | 新增 `codeLabel` 字段标签（zh/en） |
| 4 | `apps/dsa-web/e2e/fixtures/backtest-layout.spec.ts` | 新增布局回归：多视口重叠/溢出/吸顶/英文断言 + 截图 |
| 5 | `apps/dsa-web/src/pages/__tests__/BacktestPage.test.tsx` | 结构未改变查询契约，保持 11 条用例全绿 |
| 6 | `docs/CHANGELOG.md` | `[修复]` 一条用户可见说明 |
| 7 | `docs/INDEX.md` / `docs/INDEX_EN.md` | 登记本任务书 |

---

## 5. 验证矩阵

- `cd apps/dsa-web && npm run lint`
- `cd apps/dsa-web && npm run build`
- `cd apps/dsa-web && npx vitest run src/pages/__tests__/BacktestPage.test.tsx`
- `cd apps/dsa-web && npx playwright test --config playwright.fixture.config.ts e2e/fixtures/backtest-layout.spec.ts`
- `cd apps/dsa-web && npx playwright test --config playwright.fixture.config.ts`（全部 fixture，确认共享样式无回归）
- 截图人工核对 1440 / 1280 / 1024 / 768 / 390 五档。

---

## 6. 风险与回滚

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 固定高度工作区在超矮视口下把表格压得太小 | 使用 `min-height` 兜底，`<1400px` 不启用固定高度 | e2e 覆盖 768 高度 |
| `table-layout: fixed` 改变既有列宽观感 | 列宽按内容重新分配，最小宽度 768px 保证 1024 宽度无需横向滚动 | 截图前后对比 |
| 首列 sticky 与表格边框/斑马纹冲突 | 首列使用独立背景 | 深浅色各截一张图 |
| 结构改动影响既有单测查询 | 保持文本与角色不变，仅调整容器 | 先跑单测再改 CSS |

回滚方式：本次改动只涉及前端展示层，`git revert` 对应提交即可；无数据库、无 API 兼容性影响。

---

## 7. 交付说明模板

- 改了什么
- 为什么这么改（对应 1.3 根因）
- 验证情况（lint/build/vitest/e2e + 截图）
- 未验证项（如未在真实后端大数据量下验证）
- 风险点与回滚方式

---

## 8. 验证记录（2026-11-22）

| 检查 | 结果 |
| --- | --- |
| `npm run lint` | 通过 |
| `npm run build` | 通过（仅既有 chunk size 警告） |
| `npx vitest run src/pages/__tests__/BacktestPage.test.tsx` | 12/12 通过 |
| `npx playwright test --config playwright.fixture.config.ts e2e/fixtures/backtest-layout.spec.ts` | 4/4 通过（多视口 + 英文 + 吸顶滚动 + 瀑布屏） |
| `npx playwright test --config playwright.fixture.config.ts`（全量 fixture） | 39/39 通过 |
| 截图核对 | 1440 / 1280 / 1024 / 768 / 390 五档，中文与英文各一组；瀑布屏浅色 + 深色 |

说明：本机 Playwright 默认 Chromium 版本缓存不完整，验证时用同版本 Playwright 的另一个完整 Chromium 可执行文件跑同一套配置；测试代码本身不依赖该路径，CI 仍使用标准 `playwright.fixture.config.ts`。

---

## 9. 追加优化：AI 预测「瀑布屏」悬浮卡（2026-11-23）

### 9.1 问题

结果表里的 AI 预测只把「操作 / 趋势 / 建议」用 ` / ` 拼成一行放进通用 Tooltip，信息没有层级、长建议被压成一条，视觉上很廉价。

### 9.2 方案

- 通用 `Tooltip` 新增 `variant="panel"` 与 `interactive`：
  - `panel` 只负责定位 / Portal / `role="tooltip"` / 焦点与 Escape，视觉完全交给内容卡片。
  - `interactive` 在指针进入面板时取消关闭、离开后延迟 160ms 关闭，支持面板内滚动阅读长文。
- 新增 `PredictionPeek` 卡片：标题（AI 预测详情 + 代码 / 名称）、操作徽章、日期 / 阶段元信息，以及沿左侧发光渐变轨依次下落的「趋势判断 → 操作建议 → 实际复盘」三段瀑布。
- 实际复盘段展示实际涨跌与收益、方向匹配与预期、结果徽章；无复盘数据时不渲染该段。
- 动画：各段 0.38s 错峰上浮，左侧轨道 4.2s 缓慢流动；`prefers-reduced-motion` 下全部关闭。
- 深浅色分别调色：浅色为暖白渐变 + 主色描边，深色提高主色辉光并加深投影，保证与页面有分离度。
- 可访问性：触发器可聚焦，面板通过 `aria-describedby` 关联；面板文本无截断，长文可在面板内滚动。

### 9.3 验收补充

1. 悬停 / 聚焦 AI 预测单元格，`role="tooltip"` 面板出现，包含「AI 预测详情 / 趋势判断 / 操作建议 / 实际复盘」。
2. 面板宽度 ≤ 480px、完整落在视口内，长建议完整可见。
3. 指针移入面板不关闭，移出后自动关闭。
4. 中英文标签各有断言；浅色与深色各截图核对。
5. 面板不改变表格 DOM 语义、不阻断原有单元格文本与 Tooltip 焦点行为。
