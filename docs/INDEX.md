# 文档中心

这里是项目文档入口。README 负责项目概览和快速开始；更完整的配置、部署、功能说明和排障内容从这里进入。

## 目录约定

`docs/` 采用「入口文件留在根目录，主题文档进子目录」的结构：

```
docs/
├── INDEX.md / INDEX_EN.md      # 文档入口（本文件）
├── full-guide*.md              # 完整配置与部署指南（总纲）
├── DEPLOY*.md / FAQ*.md        # 部署与常见问题
├── LLM_CONFIG_GUIDE*.md        # 大模型配置总入口
├── notifications.md            # 通知能力基线（CI 契约文档）
├── alerts.md                   # 实时告警中心（CI 契约文档）
├── decision-signals.md         # 决策信号契约
├── analysis-context-pack.md    # 分析上下文包契约
├── agent/                      # Agent 事件、评测与外部 Skill 集成
├── architecture/               # API 规格产物
├── backtest/                   # 回测需求与开发文档
├── bot/                        # Bot / IM 命令、平台配置与截图
├── config/                     # 配置项、设置页与数据工具配置
├── data/                       # 数据源、市场边界、降级与情报源
├── deploy/                     # 部署与打包
├── diagnostics/                # 运行诊断分阶段记录
├── docker/                     # 容器平台部署
├── examples/                   # 可复制配置示例
├── features/                   # 功能专题与字段契约
├── signal-scanner/             # 交易信号扫描器
├── tasks/                      # 任务书与设计过程记录
└── ai/                         # 给 AI 协作的常青文档
```

新增文档时先选主题目录；只有「用户第一入口」和「被 CI / 测试直接读取的契约文档」留在根目录。

## 按场景选择

| 我想要 | 先看 | 继续看 |
| --- | --- | --- |
| 快速了解项目能做什么 | [README](../README.md) | [完整配置与部署指南](full-guide.md) |
| 第一次把项目跑起来 | [小白客户端安装与配置](beginner-client-setup.md) | [完整配置与部署指南](full-guide.md) |
| 配置大模型渠道 | [LLM 配置指南](LLM_CONFIG_GUIDE.md) | [LLM 服务商配置指南](config/llm-providers.md)（含 xAI Grok 直连） |
| 配置推送通知 | [通知能力基线](notifications.md) | [完整配置与部署指南](full-guide.md) |
| 部署到服务器或云平台 | [部署指南](DEPLOY.md) | [云端 WebUI 部署](deploy/deploy-webui-cloud.md)、[Zeabur 部署](docker/zeabur-deployment.md) |
| 使用 Bot / IM 接入 | [Bot 命令与接入](bot/bot-command.md) | [Bot 平台配置](bot/)、[Grok Bot 集成](bot/grok-bot-integration.md) |
| 排查运行问题 | [FAQ](FAQ.md) | [更新日志](CHANGELOG.md) |
| 处理数据源失败或降级 | [数据源稳定性与故障处理图示](data/data-source-stability.md) | [FAQ](FAQ.md) |
| 确认某个市场支持哪些数据 | [市场支持与边界](data/market-support.md) | [数据中心](data/data-center-diagnostics.md) |
| 参与开发或提交 PR | [贡献指南](CONTRIBUTING.md) | [API 规格](architecture/api_spec.json) |

## 快速开始

| 文档 | 内容 |
| --- | --- |
| [README](../README.md) | 项目定位、核心能力、快速开始、推送效果 |
| [小白客户端安装与配置](beginner-client-setup.md) | 面向不会代码用户的客户端下载、AIHubMix 等模型配置、新闻源配置和常见问题 |
| [完整配置与部署指南](full-guide.md) | 环境准备、运行方式、配置说明、部署路径和常见问题 |
| [FAQ](FAQ.md) | 常见配置、模型、通知、部署和运行问题 |
| [更新日志](CHANGELOG.md) | 版本变化、能力调整和迁移说明 |

## 配置

| 文档 | 内容 |
| --- | --- |
| [LLM 配置指南](LLM_CONFIG_GUIDE.md) | 大模型渠道、三层配置、Web 设置页和常见模型配置 |
| [LLM 服务商配置指南](config/llm-providers.md) | Provider 预设、Actions 映射、错误分类和诊断建议 |
| [配置单一声明源（方案）](config/config-single-source.md) | `config.py` / `config_registry.py` / `system_config_service.py` 三处配置声明的收敛方案与迁移路径 |
| [设置页配置帮助维护说明](config/settings-help.md) | 设置页帮助文案的来源、维护方式和校验边界 |
| [设置页草稿保护](config/settings-draft-protection.md) | 设置页未保存草稿的保存、丢弃、离开拦截规则 |
| [Tushare 股票列表指南](config/TUSHARE_STOCK_LIST_GUIDE.md) | Tushare 股票列表相关配置和使用说明 |
| [LiteLLM YAML 示例](examples/litellm_config.example.yaml) | LiteLLM 多渠道配置示例 |

## 数据源与市场

| 文档 | 内容 |
| --- | --- |
| [数据源稳定性与故障处理图示](data/data-source-stability.md) | TickPlus（A 股 / ETF 首选）、Tushare、TickFlow、AkShare、Efinance、YFinance、Longbridge 等已接入源的使用场景、fallback 链路和推荐配置 |
| [数据中心](data/data-center-diagnostics.md) | 只读 Provider 能力、数据集质量、精确市场支持与运行时优先级 |
| [市场支持与边界](data/market-support.md) | A股/港股/美股/日股/韩股/台股的可用数据、suffix 规则、降级边界与不承诺项 |
| [TickPlus 接入](data/tickplus.md) | TickPlus 数据源接入方式与 A 股 / ETF 场景推荐 |
| [资讯 / 情报源](data/intelligence-sources.md) | RSS/Atom 合规资讯源配置、测试、拉取、去重、存储、查询与安全边界 |
| [FXMacroData 宏观数据](data/FXMACRODATA.md) | FXMacroData 宏观数据工具的使用说明（[English](data/FXMACRODATA_EN.md)） |

## 功能专题

| 文档 | 内容 |
| --- | --- |
| [自选股变化状态与下一步](features/watchlist-next-action.md) | 首页自选股列表的变化、状态、下一步三段信号、映射规则和后续扩展边界 |
| [实时告警中心](alerts.md) | EventMonitor 基线、Web 规则管理、通知结果、冷却状态和 Phase 边界 |
| [个股研究聚合 API](features/stock-profile-api.md) | 单一 stock-profile 契约、分块质量状态、代码归一与后续 Web 边界 |
| [DecisionSignal 决策信号专题](decision-signals.md) | AI 建议池字段语义、API、Web 展示、告警/通知/组合风险联动、后验评估、脱敏、迁移与回滚 |
| [ETF 轮动](features/etf-rotation.md) | `--etf-rotation` 规则化双动量轮动：规则、配置、报告章节、参数平原解读与数据告警边界 |
| [ResearchArtifact 结构化研究产物](features/research-artifact.md) | structured_report 字段、Thesis / Evidence / Invalidation / Next Action / Data Quality 契约和旧报告兼容边界 |
| [组合风险与暴露看板](features/portfolio-risk-exposure-dashboard.md) | 持仓页风险旗标、市场暴露、币种暴露和价格质量聚合展示 |
| [选股引擎](features/screening-engine.md) | 选股规则、打分、过滤链路与配置入口 |
| [选股解释契约](features/screening-explanations.md) | Why Selected / Why Now 的后端确定性解释、来源、质量状态与缺失值边界 |
| [多策略投资建议契约](features/multi-strategy-contract.md) | Baseline 语义、Phase 1 收敛、Phase 2/3/4 边界与分歧处理 |
| [分析上下文包契约、运行态消费与可见性](analysis-context-pack.md) | AnalysisContextPack 首版范围、字段质量状态、P1/P2 内部契约、P3 Prompt 摘要消费、P4 历史/API/Web 低敏可见性、P5 数据质量评分、P6 迁移回滚与源码锚点；完整指南补充 #1386 阶段感知分析、迁移与回滚入口 |
| [飞书同源 HTML 报告](features/report-html.md) | 在报告中查看/下载与飞书推送同源的自包含 HTML：数据流、接口、安全边界与回滚 |
| [分享图片模板与数据填充](features/share-images.md) | 分享图片模板、字段填充与渲染边界 |
| [登录日志](features/login-log.md) | 管理员登录审计日志：事件类型、IP 归属地配置、客户端识别、保留策略与已知限制 |
| [图片识别 Prompt](features/image-extract-prompt.md) | 图片识别股票信息的 Prompt 与使用边界 |
| [通知能力基线](notifications.md) | 企业微信、飞书、Telegram、Discord、Slack、邮件等通知渠道配置 |

## Bot 与 Agent

| 文档 | 内容 |
| --- | --- |
| [Bot 命令与接入](bot/bot-command.md) | Bot 命令、Webhook、平台接入和回调说明（[English](bot/bot-command_EN.md)） |
| [Bot 平台配置](bot/) | 飞书、钉钉、Discord 等 Bot 配置截图和补充说明 |
| [Grok Bot 集成](bot/grok-bot-integration.md) | Grok Bot（AI teammate）与 DSA REST / DecisionSignal / Skill 的对接边界 |
| [OpenClaw Skill 集成](agent/openclaw-skill-integration.md) | OpenClaw / Skill 外部集成说明 |
| [Agent Stream 进度事件](agent/agent-stream-events.md) | Agent 流式事件的字段契约与前端消费方式 |
| [Agent Trajectory 评估](agent/agent-trajectory-eval.md) | Agent 轨迹评测的最小数据集、指标与报告结构 |

## 交易信号与回测

| 文档 | 内容 |
| --- | --- |
| [交易信号扫描器集成](signal-scanner/signal-scanner.md) | 信号扫描器的接入范围、运行方式与边界（[English](signal-scanner/signal-scanner_EN.md)） |
| [信号扫描器融合方案](signal-scanner/signal-scanner-integration-plan-20261108.md) | 2026-11-08 融合方案与阶段拆分 |
| [标普 500 超跌反弹回测](backtest/us-equity-oversold-rebound-backtest.md) | 日频超跌反弹策略的需求、规则与开发说明 |
| [成交量高潮反转做空回测](backtest/us-equity-volume-climax-reversal-short-backtest.md) | 成交量高潮反转做空策略的需求与开发任务 |

## 部署与打包

| 文档 | 内容 |
| --- | --- |
| [部署指南](DEPLOY.md) | 服务器部署、Docker、systemd、Supervisor 等部署方式 |
| [云端 WebUI 部署](deploy/deploy-webui-cloud.md) | 云服务器访问 WebUI 的部署说明 |
| [Zeabur 部署](docker/zeabur-deployment.md) | Zeabur 平台部署说明 |
| [桌面端打包说明](deploy/desktop-package.md) | Electron 桌面端和 Web 构建产物打包说明 |

## 运行诊断

| 文档 | 内容 |
| --- | --- |
| [运行诊断 Phase 0](diagnostics/run-diagnostics-p0.md) | 运行诊断与数据可靠性 1.0 的阶段目标与现状盘点 |
| [运行诊断 Phase 1](diagnostics/run-diagnostics-p1.md) | Phase 1 落地范围与验证口径 |
| [运行诊断 Phase 2](diagnostics/run-diagnostics-p2.md) | Phase 2 落地范围与验证口径 |
| [运行诊断 Phase 3](diagnostics/run-diagnostics-p3.md) | Phase 3 落地范围与验证口径 |

## 任务书与过程记录

| 文档 | 内容 |
| --- | --- |
| [任务书目录](tasks/) | 回测页排版、报告 HTML、飞书消息改版、预测市场等阶段性任务书与验收标准 |
| [回测结果区排版任务书](tasks/backtest-result-layout.md) | 回测页表头吸顶、列宽契约、窄屏卡片、AI 预测瀑布屏与筛选区响应式布局的根因、方案和验收标准 |

## 参考与开发

| 文档 | 内容 |
| --- | --- |
| [API 规格](architecture/api_spec.json) | FastAPI OpenAPI 规格产物 |
| [贡献指南](CONTRIBUTING.md) | Issue、PR、测试、文档同步和协作要求 |
| [AI 协作常青文档](ai/) | `CODEMAP` / `STATUS` / `FLOWS` / `DATA-FLOW` / `PITFALLS` |

## 多语言

| 文档 | 内容 |
| --- | --- |
| [英文文档索引](INDEX_EN.md) | English documentation index |
| [英文 README](README_EN.md) | English project overview and quick start |
| [繁中 README](README_CHT.md) | 繁體中文項目概覽與快速開始 |
