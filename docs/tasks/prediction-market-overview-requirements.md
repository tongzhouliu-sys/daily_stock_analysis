# 需求文档：预测市场全景（合约数量 / 关注焦点 / 整体态度）

> 状态：需求待评审
> 前置：P1（采集/工具/注入）与 P1.5（sidecar/报告卡片/问股证据）已合并
> 关联任务书：`docs/tasks/prediction-market-overview.md`
> 影响面：预测市场服务 / Agent 工具 / 个股分析 Prompt / 大盘复盘 Prompt / API / 测试 / 文档

---

## 1. 背景与问题

当前预测市场能力已经能回答「某个事件现在被定价为多少概率」，但用户还关心更高层的问题：

1. **预测市场现在有多少个合约？**
2. **大家都在关心什么？**（哪些事件成交最活跃、主题集中在哪）
3. **整个态度怎么样？**（风险偏好偏强还是偏弱、分歧大不大）

现有实现只做「逐事件证据」，没有聚合视角。用户需要在个股分析报告与大盘复盘里看到市场整体面貌，并在问股时得到一个简短总结。

## 2. 目标与非目标

### 2.1 目标

- G1：提供预测市场**聚合概览**：活跃合约总数、按类别/来源分布。
- G2：提供**关注焦点**：按 24h 成交额排序的热门事件、类别集中度、关注集中度。
- G3：提供**整体态度**：基于可解释的启发式分组（risk-on / risk-off）计算平均隐含概率与倾向，并明确标注「启发式、非投资建议」。
- G4：把概览注入个股分析和大盘复盘的 Prompt，让报告正文能自然写出「合约数量 / 关注焦点 / 整体态度」。
- G5：问股新增只读工具 `get_prediction_market_overview`，让 Agent 能一句话总结。
- G6：只读、fail-open、默认关闭、不参与评分/权重。

### 2.2 非目标

- 不做预测市场交易、下单或套利。
- 不把概览换算成仓位、买卖强度或 `decision_score`。
- 不新增独立页面/导航（沿用报告正文与问股回答）。
- 不新增大模型/路由/配置迁移语义。

## 3. 用户故事与验收场景

1. **个股分析**：报告正文出现类似「预测市场当前约有 N 个活跃合约，关注集中在宏观与加密；risk-on 事件平均概率 X%、risk-off 事件平均概率 Y%，整体偏中性」的总结。
2. **大盘复盘**：复盘正文包含预测市场全景（合约数量、热门事件、风险定价倾向）。
3. **问股**：用户问「预测市场现在多少个合约/大家都在关心什么/整体态度怎么样」，Agent 调用 `get_prediction_market_overview` 并给出简短总结。
4. **无数据/关闭**：不注入、不报错、不影响主流程。
5. **可追溯**：概览中的热门事件附带来源链接。

## 4. 聚合口径（确定性、可解释）

### 4.1 基础统计

- `total_contracts`：本地池中 `status=open` 且通过噪声过滤的事件数。
- `by_category`：`macro / equity / commodity / crypto` 计数。
- `by_source`：`kalshi / polymarket` 计数。

### 4.2 关注焦点

- `top_events`：按 `volume_24h` 倒序取前 5，字段含 `question / implied_probability / volume_24h / category / source / market_url`。
- `top_categories`：按计数倒序的类别分布。
- `attention_concentration`：前 5 个事件 24h 成交额 / 全部事件 24h 成交额（0–1）。

### 4.3 整体态度（启发式）

对问题文本做关键词分组（词边界匹配）：

- `risk_on`：降息、软着陆、上涨/新高、BTC/ETH 上行、财报超预期等。
- `risk_off`：衰退、加息、CPI/通胀超预期、失业率上行、违约/崩盘等。
- 其余归为 `neutral`。

计算：

- `risk_on.avg_probability` / `risk_off.avg_probability`（各自有效样本的平均隐含概率）。
- `tilt = risk_on_avg - risk_off_avg`，区间 `[-1, 1]`。
- `label`：
  - `tilt >= 0.15` → `偏 risk-on`
  - `tilt <= -0.15` → `偏 risk-off`
  - 否则 → `中性/分歧`
- 每个分组返回样本数，样本不足时明确「样本不足，仅供观察」。
- 必须附带免责声明：启发式统计，非投资建议。

> 说明：不输出「看涨/看跌」这类确定性结论；只描述风险定价的相对倾向。

## 5. 展示与注入契约

### 5.1 文本概览（Prompt 注入）

```markdown
## 预测市场全景
- 活跃合约：N 个（macro a / equity b / crypto c / commodity d；Kalshi x / Polymarket y）
- 关注焦点：1) ... 2) ... 3) ...
- 关注集中度：前 5 个合约占 24h 成交额 P%
- 风险定价倾向：risk-on 平均 R%（n 个） vs risk-off 平均 O%（m 个）→ 中性/分歧
- 免责声明：基于本地池的启发式统计，非投资建议。
```

- 个股分析：与逐事件证据一起注入 `prediction_market_context`。
- 大盘复盘：与宏观/加密/商品事件一起注入复盘 Prompt。

### 5.2 结构化概览（API）

`GET /api/v1/prediction-markets/overview` 返回：

```json
{
  "enabled": true,
  "total_contracts": 214,
  "by_category": {"macro": 112, "equity": 47, "crypto": 55, "commodity": 0},
  "by_source": {"kalshi": 181, "polymarket": 33},
  "top_events": [{"question": "...", "implied_probability": 0.62, "volume_24h": 420000, "category": "macro", "source": "kalshi", "market_url": "..."}],
  "attention_concentration": 0.41,
  "attitude": {"label": "中性/分歧", "tilt": 0.02, "risk_on": {"count": 20, "avg_probability": 0.55}, "risk_off": {"count": 15, "avg_probability": 0.53}},
  "generated_at": "2026-11-23T..."
}
```

- 未启用时返回 `enabled: false` 与空统计，不报错。

## 6. 边界与合规

- 只读、fail-open、默认关闭。
- 不参与评分/权重/仓位。
- 概览为启发式统计，必须带免责声明。
- 不泄露敏感信息；热门事件只含公开低敏字段与来源链接。

## 7. 分期

| 阶段 | 内容 |
| --- | --- |
| P2.0（本需求） | 聚合服务 + 概览文本注入 + 问股工具 + API |
| P2.1（可选） | Web 报告卡片增加概览摘要行 |
| P2.2（可选） | 概率历史趋势与关注度变化 |

## 8. 开放问题（评审确认）

1. 「整体态度」是否接受上述启发式 risk-on/risk-off 口径，还是只做中性统计、不输出倾向？
2. 概览是否要覆盖全部平台/市场，还是按股票所属市场过滤（如美股只看 us/global）？
3. 问股工具返回结构化数据即可，还是需要固定话术模板？
