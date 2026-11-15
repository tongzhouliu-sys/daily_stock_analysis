---
version: 1.0
name: DSA-web
description: "Warm-paper research workspace: off-white canvas, copper as the only brand accent, 2–3px corners, hairline borders, flat fills, and a system sans stack with CJK fallbacks and tabular figures for market data. Depth is reserved for transient menus and dialogs. Light and dark modes are both first-class."

colors:
  primary: "hsl(15 69% 37%)"          # 铜色，唯一品牌强调色
  primary-hex: "#a03e1d"              # 近似值，仅供设计工具使用
  on-primary: "hsl(43 42% 98%)"
  canvas: "hsl(40 23% 95%)"           # 暖纸底色
  card: "hsl(45 33% 99%)"
  elevated: "hsl(42 33% 98%)"
  hover: "hsl(35 24% 90%)"
  ink: "hsl(30 13% 13%)"
  ink-secondary: "hsl(33 11% 33%)"
  ink-muted: "hsl(33 10% 40%)"
  secondary: "hsl(36 19% 90%)"
  border: "hsl(35 15% 83%)"
  input-border: "hsl(34 13% 65%)"
  success: "hsl(156 61% 28%)"
  warning: "hsl(36 95% 29%)"
  danger: "hsl(0 67% 41%)"
  neutral-sentiment: "hsl(185 27% 32%)"

typography:
  family-sans:
    fontFamily: 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif'
    note: '实现里 IBM Plex Sans 仅作为本机已安装时的首选，不加载 webfont；实际渲染以系统字体为准'
  family-mono:
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
  base:
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0.01em
  numeric:
    fontVariantNumeric: tabular-nums
    note: '价格、涨跌幅、成交量等市场数据必须使用等宽数字'

rounded:
  xs: 2px
  sm: 2px
  md: 2px
  lg: 2px
  xl: 2px
  2xl: 3px
  3xl: 4px
  token: 3px   # --radius

spacing:
  base: 4px
  xxs: 4px
  xs: 8px
  sm: 12px
  md: 16px
  lg: 24px
  xl: 32px
  section: 96px

elevation:
  flat: '无阴影，hairline 边框'
  menu: 'var(--shadow-soft-card)，仅用于浮层菜单与对话框'
  focus: '2px 品牌色 outline + 2px offset'

components:
  panel:
    backgroundColor: "{colors.card}"
    border: '1px solid {colors.border}'
    rounded: "{rounded.md}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
  button-secondary:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    border: '1px solid {colors.border}'
    rounded: "{rounded.md}"
  data-table:
    backgroundColor: "{colors.card}"
    textColor: "{colors.ink}"
    headerTextColor: "{colors.ink-muted}"
    border: '1px solid {colors.border}'
  chat-bubble-user:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
---

# DSA Web 设计说明

本文只描述**已经上线的界面实际使用的变量**。变量的唯一真源是代码：

| 类别 | 真源 |
| --- | --- |
| 颜色、字体、圆角、CSS 变量 | `apps/dsa-web/src/index.css` |
| Tailwind 主题扩展（圆角、字号、阴影、动画） | `apps/dsa-web/src/tailwind.config.js` |
| 报告 / 问股专用样式 | `apps/dsa-web/src/research.css` |
| 报告语言与固定文案 | `src/report_language.py`、`apps/dsa-web/src/i18n/uiText.ts` |

改设计变量时先改代码，再同步本文；两边不一致以代码为准，并顺手修正本文。

## 1. 视觉基调

- **暖纸 + 铜色**：浅色模式画布是暖白（`hsl(40 23% 95%)`），品牌强调色只有一种铜色（`hsl(15 69% 37%)`）。涨跌、状态色与品牌色相互独立，不允许用铜色表达涨跌。
- **近方形**：圆角集中在 2–3px（`xs`–`xl` 为 2px，`2xl` 为 3px，`3xl` 为 4px），不使用胶囊按钮与大圆角卡片。
- **扁平优先**：卡片靠 1px hairline 边框与底色差异分层；阴影只用于浮层菜单、弹层和对话框。
- **信息密度**：研究台优先铺满可用宽度，报告阅读区在左、任务 / 历史 / 自选在右；不做大面积留白营销页。

## 2. 字体

- 正文使用系统字体栈：`system-ui → PingFang SC → Microsoft YaHei → Helvetica Neue`，中文不依赖 webfont。
- 等宽数字（`tabular-nums`）用于价格、涨跌幅、成交量、持仓数量等所有对齐比较的数值。
- 代码与等宽文本使用 `ui-monospace` 栈。
- 不引入需要联网加载的字体，离线部署与桌面端必须保持一致渲染。

## 3. 布局与断点

- 4px 基础栅格；间距档位 4 / 8 / 12 / 16 / 24 / 32 / 96。
- 桌面端：侧栏 + 主内容双列；移动端：导航与研究列表分别进抽屉，主区单列。
- 数据表格在窄屏横向滚动，不折叠关键列；滚动区域具备键盘可达性。
- 焦点可见：2px 品牌色 outline，不允许 `outline: none` 而无替代。

## 4. 组件约定

- 面板：`card` 底色 + 1px `border`，圆角 2px，无阴影。
- 按钮：主按钮铜色底 + 浅色文字；次按钮 card 底 + 边框 + ink 文字；危险操作使用 `danger` 色，不用品牌色。
- 表格：表头使用 `ink-muted`，行分隔使用 hairline；数值右对齐并使用等宽数字。
- 问股：用户气泡使用品牌色；AI 消息使用 card 底 + prose 排版；进度区只展示最近若干步，历史消息不因输入而重渲染。
- 空状态：一句说明 + 快捷问题卡片，不使用插画大图。

## 5. 明暗模式与可访问性

- 明暗两套变量都定义在 `src/index.css`，新增变量必须同时提供两套值。
- 只依赖变量的组件不需要额外适配；直接写死颜色的地方按缺陷处理。
- 支持 `prefers-reduced-motion`：进度动画与滚动动效必须可关闭。
- 涨跌色不作为唯一信息载体，需同时有符号或文案。

## 6. 与 IBM Carbon 的关系

仓库早期曾以 IBM Carbon 作为设计参考，并保留了一份完整的 Carbon 变量与文案规范。该参考与实际上线界面（暖纸、铜色、2–3px 圆角、系统字体）不一致，已删除，避免继续误导实现。

需要查 Carbon 原始规范时，以 [Carbon Design System 官方文档](https://carbondesignsystem.com/) 为准，不要从本仓库的历史副本推断产品决策。
