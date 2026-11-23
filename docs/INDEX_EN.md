# English Documentation Index

This is the entry point for project documentation. The README covers the project overview and quick start; detailed setup, configuration, deployment, feature usage, and troubleshooting docs are linked below.

> For Chinese documentation, see [docs/INDEX.md](INDEX.md).

## Directory Layout

`docs/` keeps entry-point files at the root and groups topic docs into subfolders:

```
docs/
├── INDEX.md / INDEX_EN.md      # documentation entry points
├── full-guide*.md              # complete configuration & deployment guide
├── DEPLOY*.md / FAQ*.md        # deployment and FAQ
├── LLM_CONFIG_GUIDE*.md        # LLM configuration entry point
├── notifications.md            # notification baseline (CI contract doc)
├── alerts.md                   # real-time alert center (CI contract doc)
├── decision-signals.md         # decision signal contract
├── analysis-context-pack.md    # analysis context pack contract
├── agent/                      # agent events, evaluation, external skill integration
├── architecture/               # API spec artifacts
├── backtest/                   # backtest requirements and dev docs
├── bot/                        # bot / IM commands, platform setup, screenshots
├── config/                     # configuration, settings page, data tool config
├── data/                       # data sources, market boundaries, intelligence sources
├── deploy/                     # deployment and packaging
├── diagnostics/                # run diagnostics phase records
├── docker/                     # container platform deployment
├── examples/                   # copy-ready examples
├── features/                   # feature topics and field contracts
├── signal-scanner/             # trading signal scanner
├── tasks/                      # task briefs and design records
└── ai/                         # evergreen docs for AI collaboration
```

## Choose By Goal

| I want to | Start with | Then read |
| --- | --- | --- |
| Understand what the project does | [README (EN)](README_EN.md) | [Full Guide (EN)](full-guide_EN.md) |
| Run the project for the first time | [README (EN)](README_EN.md) | [Full Guide (EN)](full-guide_EN.md) |
| Configure model providers | [LLM Config Guide (EN)](LLM_CONFIG_GUIDE_EN.md) | [Provider Configuration Guide](config/llm-providers.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) |
| Configure notifications | [Notification Baseline](notifications.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | [Full Guide (EN)](full-guide_EN.md) |
| Deploy to a server or cloud platform | [Deploy Guide (EN)](DEPLOY_EN.md) | [Cloud WebUI Deployment](deploy/deploy-webui-cloud.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only), [Zeabur Deployment](docker/zeabur-deployment.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) |
| Use Bot / IM integrations | [Bot Commands (EN)](bot/bot-command_EN.md) | [Bot Platform Docs](bot/) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) |
| Troubleshoot runtime issues | [FAQ (EN)](FAQ_EN.md) | [Changelog](CHANGELOG.md) |
| Troubleshoot data-source failures | [Data-source stability diagrams](data/data-source-stability.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | [FAQ (EN)](FAQ_EN.md) |
| Confirm what a market supports | [Market Support And Boundaries](data/market-support.md) (Chinese-only) | [Data Center](data/data-center-diagnostics.md) (Chinese-only) |
| Contribute code or docs | [Contributing Guide (EN)](CONTRIBUTING_EN.md) | [API Spec](architecture/api_spec.json) |

## Getting Started

| Document | Contents |
| --- | --- |
| [README (EN)](README_EN.md) | Project overview, key features, quick start, sample output |
| [Full Guide (EN)](full-guide_EN.md) | Environment setup, run modes, configuration, deployment paths, and common issues |
| [FAQ (EN)](FAQ_EN.md) | Common configuration, model, notification, deployment, and runtime issues |
| [Changelog](CHANGELOG.md) | Release notes, capability changes, and migration notes |

## Configuration

| Document | Contents |
| --- | --- |
| [LLM Config Guide (EN)](LLM_CONFIG_GUIDE_EN.md) | Model providers, three-tier configuration, Web settings, and common model setup |
| [Provider Configuration Guide](config/llm-providers.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Provider presets, GitHub Actions mapping, error categories, and diagnostics |
| [Single-Source Config Proposal](config/config-single-source.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Proposal for collapsing the three config declaration sites into one table, with migration stages and compatibility notes |
| [Settings Help Maintenance](config/settings-help.md) (Chinese-only) | Where settings-page help text comes from and how it is validated |
| [Settings Draft Protection](config/settings-draft-protection.md) (Chinese-only) | Save/discard/leave-guard rules for unsaved settings drafts |
| [Tushare Stock List Guide](config/TUSHARE_STOCK_LIST_GUIDE.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Tushare stock-list configuration and usage notes |
| [LiteLLM YAML Example](examples/litellm_config.example.yaml) | Example LiteLLM multi-provider configuration |

## Data Sources And Markets

| Document | Contents |
| --- | --- |
| [Data-source stability diagrams](data/data-source-stability.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | TickPlus (preferred for A-shares / ETFs), existing-source routing, fallback chains and configuration profiles |
| [Data Center](data/data-center-diagnostics.md) (Chinese-only) | Read-only provider capabilities, exact dataset/market support, quality states, and runtime priorities |
| [Market Support And Boundaries](data/market-support.md) (Chinese-only) | Market coverage, suffix rules, degraded capabilities, and explicit non-goals for A/H/US/JP/KR/TW stocks |
| [TickPlus Integration](data/tickplus.md) (Chinese-only) | TickPlus data-source setup and A-share / ETF recommendations |
| [Intelligence Sources](data/intelligence-sources.md) (Chinese-only) | RSS/Atom source configuration, testing, fetching, deduplication, storage, queries, and safety boundaries |
| [FXMacroData](data/FXMACRODATA_EN.md) | Macro data tooling reference (also available in [Chinese](data/FXMACRODATA.md)) |

## Feature Topics

| Document | Contents |
| --- | --- |
| [Watchlist Change, State, And Next Action](features/watchlist-next-action.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Three compact signals shown for each stock in the home-page watchlist, their mapping rules, and the boundary for later extensions |
| [Real-Time Alert Center](alerts.md) <sub><sub>![P4 Badge](https://img.shields.io/badge/P4-yellow?style=flat)</sub></sub> (Chinese-only) | EventMonitor baseline, Web rule management, notification attempts, cooldown state, and phase boundaries |
| [Stock Profile API](features/stock-profile-api.md) <sub><sub>![P1 Badge](https://img.shields.io/badge/P1-yellow?style=flat)</sub></sub> (Chinese-only) | Single stock-profile contract, per-block quality status, code normalization, and Web phase boundaries |
| [DecisionSignal Topic](decision-signals.md) <sub><sub>![P7 Badge](https://img.shields.io/badge/P7-orange?style=flat)</sub></sub> (Chinese-only) | AI signal fields, API, Web display, alert/notification/portfolio-risk linkage, outcome evaluation, redaction, migration, and rollback |
| [ETF Rotation](features/etf-rotation.md) (Chinese-only) | Rule-based dual-momentum ETF rotation via `--etf-rotation`: rules, config, report sections, parameter-plateau reading, and data-quality warnings |
| [ResearchArtifact Contract](features/research-artifact.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Structured thesis, evidence, invalidation conditions, deterministic fallback identity, and the boundary before persistence/API integration |
| [Portfolio Risk And Exposure Dashboard](features/portfolio-risk-exposure-dashboard.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Portfolio-page risk flags, market/currency exposure, classified-sector handling, and price-quality aggregation |
| [Screening Engine](features/screening-engine.md) (Chinese-only) | Screening rules, scoring, filter pipeline, and configuration entry points |
| [Screening Explanation Contract](features/screening-explanations.md) <sub><sub>![P5 Badge](https://img.shields.io/badge/P5-orange?style=flat)</sub></sub> (Chinese-only) | Deterministic Why Selected / Why Now explanations, provenance, quality, and missing-value boundaries |
| [Multi-Strategy Contract](features/multi-strategy-contract.md) (Chinese-only) | Baseline semantics, Phase 1 convergence, Phase 2/3/4 boundaries, and disagreement handling |
| [Analysis Context Pack Contract, Runtime Consumption, And Visibility](analysis-context-pack.md) <sub><sub>![P6 Badge](https://img.shields.io/badge/P6-orange?style=flat)</sub></sub> (Chinese-only) | AnalysisContextPack first-scope boundaries, field quality states, P1/P2 internal contracts, P3 prompt-summary consumption, P4 history/API/Web low-sensitivity visibility, P5 data-quality scoring, and P6 migration/rollback notes, plus source anchors; the full guide adds #1386 market-phase analysis, migration, and rollback entry points |
| [Feishu-aligned HTML Report](features/report-html.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | View/download a self-contained HTML report aligned with Feishu pushes: data flow, API, security, rollback |
| [Share Images](features/share-images.md) (Chinese-only) | Share-image templates, field population, and rendering boundaries |
| [Login Audit Log](features/login-log.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Admin login audit log: event types, IP geolocation config, client identification, retention policy, and known limitations |
| [Image Extraction Prompt](features/image-extract-prompt.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Prompt and boundaries for extracting stock information from images |
| [Notification Baseline](notifications.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | WeChat Work, Feishu, Telegram, Discord, Slack, Email, and other notification channels |

## Bots And Agents

| Document | Contents |
| --- | --- |
| [Bot Commands (EN)](bot/bot-command_EN.md) | Bot commands, webhooks, platform integration, and callback behavior |
| [Bot Platform Docs](bot/) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Feishu, DingTalk, Discord, and related Bot configuration screenshots and notes |
| [Grok Bot Integration](bot/grok-bot-integration.md) <sub><sub>![P0 Badge](https://img.shields.io/badge/P0-green?style=flat)</sub></sub> (Chinese-only) | 2026-08-11 Grok Bot (AI teammate) vs LiteLLM `xai/` provider; HTTP / Skill / DecisionSignal consumption |
| [OpenClaw Skill Integration](agent/openclaw-skill-integration.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | OpenClaw / Skill external integration notes |
| [Agent Stream Events](agent/agent-stream-events.md) (Chinese-only) | Streaming event field contracts and frontend consumption |
| [Agent Trajectory Evaluation](agent/agent-trajectory-eval.md) (Chinese-only) | Minimal trajectory dataset, metrics, and report structure |

## Signal Scanner And Backtests

| Document | Contents |
| --- | --- |
| [Signal Scanner](signal-scanner/signal-scanner_EN.md) | Signal scanner integration scope, run modes, and boundaries |
| [Signal Scanner Integration Plan](signal-scanner/signal-scanner-integration-plan-20261108.md) (Chinese-only) | 2026-11-08 integration plan and phase breakdown |
| [US Equity Oversold Rebound Backtest](backtest/us-equity-oversold-rebound-backtest.md) (Chinese-only) | Requirements, rules, and development notes for the daily oversold-rebound strategy |
| [US Equity Volume Climax Reversal Short Backtest](backtest/us-equity-volume-climax-reversal-short-backtest.md) (Chinese-only) | Requirements and dev tasks for the volume-climax reversal short strategy |

## Deployment And Packaging

| Document | Contents |
| --- | --- |
| [Deploy Guide (EN)](DEPLOY_EN.md) | Server deployment, Docker, systemd, Supervisor, and related options |
| [Cloud WebUI Deployment](deploy/deploy-webui-cloud.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Cloud server WebUI access and deployment notes |
| [Zeabur Deployment](docker/zeabur-deployment.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Zeabur platform deployment |
| [Desktop Packaging](deploy/desktop-package.md) <sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub> (Chinese-only) | Electron desktop app and Web artifact packaging |

## Run Diagnostics

| Document | Contents |
| --- | --- |
| [Run Diagnostics Phase 0](diagnostics/run-diagnostics-p0.md) (Chinese-only) | Goals and current-state inventory for run diagnostics and data reliability 1.0 |
| [Run Diagnostics Phase 1](diagnostics/run-diagnostics-p1.md) (Chinese-only) | Phase 1 scope and verification criteria |
| [Run Diagnostics Phase 2](diagnostics/run-diagnostics-p2.md) (Chinese-only) | Phase 2 scope and verification criteria |
| [Run Diagnostics Phase 3](diagnostics/run-diagnostics-p3.md) (Chinese-only) | Phase 3 scope and verification criteria |

## Task Briefs

| Document | Contents |
| --- | --- |
| [Task Briefs Directory](tasks/) (Chinese-only) | Backtest layout, HTML report, Feishu message redesign, prediction market, and other phase task briefs with acceptance criteria |
| [Backtest Result Layout Task](tasks/backtest-result-layout.md) (Chinese-only) | Sticky header, column-width contract, compact card list, responsive filter grid, and the AI prediction waterfall hover card |

## Reference And Development

| Document | Contents |
| --- | --- |
| [API Spec](architecture/api_spec.json) | FastAPI OpenAPI artifact |
| [Contributing Guide (EN)](CONTRIBUTING_EN.md) | Issues, pull requests, tests, documentation sync, and collaboration expectations |
| [AI Collaboration Docs](ai/) | `CODEMAP` / `STATUS` / `FLOWS` / `DATA-FLOW` / `PITFALLS` |

## Languages

| Document | Contents |
| --- | --- |
| [Chinese Documentation Index](INDEX.md) | Chinese documentation entry point |
| [Traditional Chinese README](README_CHT.md) | Traditional Chinese project overview and quick start |

## China-Market Glossary

| Term | Meaning |
| --- | --- |
| **A-shares** | Stocks listed on the Shanghai or Shenzhen stock exchanges, denominated in CNY |
| **Northbound capital flow** | Net buy/sell flow from foreign investors through Stock Connect programs |
| **Dragon-Tiger List** | Daily SSE/SZSE disclosure of heavily traded stocks and top trading seats |
| **Chip distribution** | Cost-basis distribution of outstanding shares, often used to estimate support and resistance |
| **Tushare** | Chinese financial data API that requires a token |
| **AkShare** | Open-source Python market data library |
| **Baostock** | Free Python SDK for historical A-share data |
| **WeChat Work** | Tencent enterprise messaging platform with webhook notifications |
| **Feishu** | ByteDance enterprise collaboration platform with webhook notifications |
| **PushPlus / ServerChan** | Chinese mobile push notification services |
