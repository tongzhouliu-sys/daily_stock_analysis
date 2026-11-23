# 开发任务书：飞书卡片「决策仪表盘开头」与「分析结果摘要」视觉打磨

> 状态：已实施（P1.2）
> 基线：`main` 当前 `src/notification_sender/feishu_card.py`（原生 table 方案已落地，见 `docs/tasks/feishu-message-redesign.md` P1 / P1.1）
> 任务类型：`feat`（视觉打磨）+ `docs`
> 影响面：`src/notification_sender/feishu_card.py`、`tests/test_feishu_card.py`、`docs/notifications.md`、`docs/CHANGELOG.md`
> 关联：卡片与报告 HTML 共用解析（`docs/features/report-html.md`），本任务不改解析契约，仅改渲染呈现

---

## 1. 背景与现状

飞书卡片已改用原生 `table` 组件承载数值内容，但用户反馈两块仍不美观：

- **决策仪表盘开头**：标题栏 + 信号区。
- **分析结果摘要**：多标的摘要表。

两块的可读性、信息密度、视觉层次都不足，原因是「语义信息没有视觉锚点」。

### 1.1 现状（已核实）

**A. 决策仪表盘开头**

| 位置 | 现状 | 问题 |
| --- | --- | --- |
| 副标题 | `共分析 1 只股票 | 🟢买入:0 🟡观望:1 🔴卖出:0` | 计数挤成一行文字，无层次 |
| 信号区第一行 | `column_set`，内容 `**操作**\n<text_tag color='grey'>观望</text_tag>` 等 | 「加粗标签 + 换行 + 值」像文字不像仪表盘 |
| 动作色板 | `_signal_action_color()`：非买/卖一律 `grey` | 「观望 / 持有」这类中性动作用了 Feishu 语义里的「失效灰」，视觉上等于没给结论 |
| 信号区第二行 | `**🎯 理想买**\n1450` | 数字裸奔，无强调、无单位 |

**B. 分析结果摘要**

- `_summary_table()` 四列 `标的 / 动作 / 评分 / 趋势` 全部 `data_type: text`，纯单色文本。
- 标的列带 `⚪/🟢` 信号 emoji，与「动作」语义重复。
- 评分列是纯数字，无右对齐、无分级色、无 `/100` 量纲。
- `_build_group_elements()` 摘要分支只在 `len(summary_rows) >= 2` 时渲染表格，单只股票回退 `fields` 网格，两种形态不一致、单股摘要没有彩色锚点。

## 2. 目标与非目标

### 2.1 目标

- G1：摘要表的「动作」用彩色选项标签（`options`），「评分」用分级色 + `/100` 居中，「趋势」保持文本；标的去掉信号 emoji，让「结论」一眼可见。
- G2：副标题只保留「共分析 N 只股票」，把 🟢/🟡/🔴 计数提升为标题栏三枚彩色后缀标签（`text_tag_list`）。
- G3：信号区改为「灰色小字标签 + 强调值」**同行展示**（数字不换行）：动作彩色徽章、评分彩色加粗、趋势加粗、点位标签灰字 + 数值强调。
- G4：动作色板补齐「观望 / 持有」→ 橙色（不再灰），与评分中性带橙色保持一致。
- G5：单只股票的「分析结果摘要」也渲染成单行表格，与多股形态一致。
- G6：个股数值（行情 / 技术 / 点位 / 盘中）不再用「分类 / 指标 / 数值」三列表，改为**按分类拆小版块**：每类一个灰色分类标题 + 行内「指标 数值」双列（数字不换行）。
- G7：降级链（富 / 轻 / 紧凑 / 纯文本）与信息完整性不变；`options` / `lark_md` 在低版本客户端降级为纯文本时仍可读。

### 2.2 非目标

- 不升级 Card 2.0，保持 Card JSON 1.0（`table` 已是 1.0 原生）。
- 不改结构化数据产出、评分 / 买卖逻辑阈值（沿用 `_signal_score_color()`，仅扩展 `_signal_action_color()` 补中性色）。
- 不改正文分节、页脚、免责声明；长策略 / 观察条件等长文本仍走全宽字段或正文。
- 不新增用户可配置开关。

## 3. 设计

### 3.1 动作色板（单一来源，信号区与摘要表共用）

新增/调整一个映射函数，输入动作文本，输出标签色：

| 动作 | 颜色 | 命中词 |
| --- | --- | --- |
| 买入 / 加仓 | `green` | 买 / 加仓 / buy / add / long |
| 卖出 / 减仓 / 回避 | `red` | 卖 / 减仓 / 回避 / sell / reduce / avoid / short |
| 观望 / 持有 / 中性 | `orange` | 观望 / 持有 / 中性 / watch / hold / neutral |
| 其他 | `grey` | 兜底 |

`_signal_action_color()` 同步使用该映射（原先中性返回 `grey`，改为 `orange`）。

### 3.2 摘要表列定义（`_summary_table()`）

| 列 | data_type | 对齐 | 说明 |
| --- | --- | --- | --- |
| 标的 | `text` | left | 去掉行首信号 emoji，只留「名称(代码)」 |
| 动作 | `options` | center | `[{"text": 动作, "color": 色板}]`；动作缺失时给空字符串 |
| 评分 | `lark_md` | center | `<font color='分级色'>**N**</font>/100`，分级色沿用 `_signal_score_color()`（>60 绿 / <40 红 / 其余橙） |
| 趋势 | `text` | left | 原样 |

兼容性：`options` 与 `table` 同为飞书 7.4+；`lark_md` 为 7.10+，低于该版本该列降级为纯文本（数字仍可见），不丢信息。若线上出现老客户端渲染异常，回退动作是把「评分」列 `data_type` 改回 `text`/`number`（一行改动）。

### 3.3 副标题 + 标题后缀标签（`_header()`）

- 从摘要行提取计数，渲染 `header.text_tag_list`（最多 3 枚）：
  - `🟢 买入 n` → `green`
  - `🟡 观望 n` → `yellow`
  - `🔴 卖出 n` → `red`
- 副标题只保留「共分析 N 只股票」（去掉计数段）。

### 3.4 信号区呈现（`_render_stock_signal_zone()`）

- 标签灰字 + 值强调，**同行展示（数字不换行）**，例如 `操作 <text_tag>观望</text_tag>`：
  - 动作：`<text_tag color='色板色'>动作</text_tag>`
  - 评分：`<font color='分级色'>**N**</font>/100`
  - 趋势：`**趋势**`
  - 点位：`🎯 理想买 **1450**`
- 仍用 `column_set` 并排（1.0 `column` 无背景能力，不强行造卡片）。

### 3.5 个股数值版块（`_metric_block_elements()`，替代 `_metric_table()`）

- 把 `_collect_metric_rows()` 收集的数值行按分类分组，每类渲染：
  - 一个灰色分类标题 `<font color='grey'>**行情**</font>`
  - 一个 `fields` 双列网格，单元格内容为行内「**指标** 数值」（`_fields_element(inline=True)`，数字不换行）
- 保留原有收集规则：同名指标只保留首次、长文本不进版块、按「点位 → 行情 → 技术 → 盘中」排序。

## 4. 改动清单

- `src/notification_sender/feishu_card.py`
  - `_signal_action_color()` 补中性动作 → `orange`。
  - `_summary_table()` 改列定义为 `options` / `lark_md`，标的去 emoji，评分带色 + `/100`。
  - `_build_group_elements()` 摘要分支把 `len(summary_rows) >= 2` 放宽为 `>= 1`；个股数值分支 `_metric_table()` → `_metric_block_elements()`（按分类拆版块）。
  - `_fields_element()` 新增 `inline` 参数：行内「**指标** 数值」，数字不换行。
  - `_header()` 新增计数 → `text_tag_list`，副标题只留数量。
  - `_columns_element()` 与 `_render_stock_signal_zone()` / `_render_market_signal_zone()` 改为标签灰字 + 值强调同行展示。
- `tests/test_feishu_card.py`：新增动作色板、摘要表列类型 / 评分带色、标题后缀标签、单股摘要表、分类版块、信号区同行断言；更新受影响旧断言。
- `docs/notifications.md`：更新「飞书消息卡片排版」摘要表、个股版块与标题栏描述。
- `docs/CHANGELOG.md`：`[Unreleased]` 追加扁平条目。

## 5. 验收标准

- 摘要表：动作是彩色标签、评分带分级色 + `/100` 且居中、标的无信号 emoji、单只股票也出表。
- 副标题：只含「共分析 N 只股票」，计数在标题栏三枚彩色标签里。
- 信号区：标签灰字、动作/评分带色、点位数值加粗；「观望」显示橙色而非灰色。
- 降级：无 `render_context` 时输出不丢信息；`options` / `lark_md` 老客户端降级为文本仍可读。
- 回归：`tests/test_feishu_card.py`、`tests/test_formatters.py`、`tests/test_notification_sender.py`、`tests/test_feishu_card_document.py`、`tests/test_report_html_renderer.py` 全绿。

## 6. 风险与回滚

| 风险 | 影响 | 缓解 / 回滚 |
| --- | --- | --- |
| `lark_md` 列需飞书 7.10（高于 `table` 的 7.4） | 老客户端该列降级为纯文本，颜色丢失但数字仍在 | 回退为 `number`/`text` 单行改动 |
| `options` 文本过长截断 | 动作词 2–4 字，安全 | 动作统一走 `_signal_text` 截断 |
| 标的去 emoji 后颜色信号弱化 | 老客户端动作列降级为文本时颜色丢失 | 动作文字本身仍在，语义不丢 |
| 单股摘要多占一张表 | 单股报告本就只有 1 张数值表，总表数 ≤2，未触 5 表上限 | — |

## 7. 交付要求

按仓库默认交付结构说明：改了什么 / 为什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式；PR 描述附受影响卡片前后对比截图（无法截图时说明原因与替代可视证据）。
