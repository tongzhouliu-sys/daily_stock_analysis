# STATUS · 能力现状与改动禁区

> 手写维护。每个阶段结束（发版 / 里程碑合入）时更新一次。
> 目的：新会话只读 `CODEMAP.md` + `STATUS.md` 就能判断「这个能力是不是默认开着」「这个文件能不能大改」。

## 1. 默认开启的能力（不需要额外配置）

| 能力 | 说明 | 代码锚点 |
| --- | --- | --- |
| 报告对象统一构造 | 同步分析、任务状态回退、历史详情返回同一份 `meta/summary/strategy/details` | `api/v1/report_builder.py` |
| 通知统一分发 | 所有渠道走 `send_with_results()`，逐渠道隔离失败并写诊断 | `src/notification.py` |
| 问股守卫规则层 | 规则判不出时直接放行，不再默认调模型分类 | `api/v1/endpoints/agent.py`（`_check_domain_guard`） |
| 问股标的绑定 | 会话已有标的时，只有显式切换词、股票代码或「名称 + 提问谓语」（如「宁德时代怎么样」）才触发切换确认；仅提及其他名称按对比对象处理，不替换当前标的。口径已定（R9），由 `tests/test_agent_active_stock_integration.py::test_established_object_subject_question_contract` 固定 | `src/agent/chat_object_decision.py` |
| 首问引导（intake v2） | 新会话首问在创建草稿时用本地索引解析标的、推断背景；标的明确或不需要标的时直接开流，只有标的不明确才问一道带候选的题；小写代码等价、港股短码补零、索引外代码转 `unknown_code` 确认、行情/板块/选股类问题不拦截；旧页面仍可发 `interaction_version=1` | `api/v1/endpoints/agent.py` `/chat/intake`、`src/agent/object_resolution.py`、`src/agent/intake_questions.py` |
| 问股输入标的引导 | 输入框「选股票」面板与被动联想、空状态自选 chip 与格式提示、理解卡按原因分形态 + 不指定股票出口、会话中「讨论中」chip 换股 | `apps/dsa-web/src/components/chat/ChatStockPicker.tsx`、`ChatInputArea.tsx`、`ChatUnderstandingCard.tsx`、`utils/stockMention.ts` |
| 首页运行反馈 | 主阅读区运行卡 + 任务面板：五阶段、当前步骤、已用时、取消；失败停留可重试，断线提示并加密轮询 | `apps/dsa-web/src/components/tasks/AnalysisRunCard.tsx`、`src/services/analysis_progress.py` |
| 问股运行反馈 | 发送即上屏；进行中面板三阶段 + 步骤时间线 + 停止；心跳静默 3 个周期转入进度恢复 | `apps/dsa-web/src/components/chat/ChatLiveProgress.tsx`、`src/stores/agentChatStore.ts` |
| 数据新鲜度标注 | 日线落后时报告写「截至 X（落后 N 个交易日）」并降级数据块 | `src/analyzer.py`、`src/core/pipeline.py` |
| 分析任务取消 | `POST /api/v1/analysis/tasks/{task_id}/cancel`，排队即取消、运行中在进度检查点中止 | `api/v1/endpoints/analysis.py` |
| 问股断开不取消 | 刷新/关页只断开 SSE，后台继续跑完；取消只认停止接口，断开后取消入口保留到执行体退出 | `api/v1/endpoints/agent.py`、`src/agent/cancellation.py` |
| 选股单飞 | 已有 `pending/processing/cancel_requested` 选股任务时再次提交返回 409 | `api/v1/endpoints/screening.py` |
| 问股页面拆分与记忆化 | 侧栏 / 消息 / 进度 / 输入 / 引导卡独立组件，打字不重渲染历史消息 | `apps/dsa-web/src/components/chat/` |
| 问股手机端形态 | 手机宽度（<48rem）下顶栏带文字的「历史」与「新对话」；历史面板可搜索、每行显示最近活跃时间与消息条数；会话操作为底部面板；消息操作条跟随各自消息；长链接折行、宽表格在自身容器内横滑；回车换行、输入框 16px | `apps/dsa-web/src/pages/ChatPage.tsx`、`components/chat/ChatSessionSidebar.tsx`、`components/common/BottomSheet.tsx`、`hooks/useMediaQuery.ts`、`research.css`、`e2e/fixtures/chat-mobile.spec.ts` |
| 首次设置令牌 | 未设密码时，公网客户端必须提供 `ADMIN_SETUP_TOKEN` 或启动日志中的随机令牌；回环/私网仍可零配置初始化 | `src/auth.py`、`api/v1/endpoints/auth.py` |
| 飞书同源 HTML 报告 | 历史报告分享菜单可查看 / 下载与飞书推送同源的自包含 HTML；卡片与 HTML 共用解析，零脚本 + 沙箱 iframe；HTML 固定全中文，en/ko 记录使用持久化中文变体，缺变体时 409 + Web 生成按钮 | `src/services/report_html/`、`src/services/report_translation_service.py`、`api/v1/endpoints/history.py`、`src/notification_sender/feishu_card.py` |

**默认关闭**：`WEBUI_ENABLED=false`、`SCHEDULE_ENABLED=false`；通知渠道默认未配置，未配置就不发送。自动打 tag 需要 commit title 含 `#patch/#minor/#major`。

## 2. 已完成里程碑

| 任务 | 内容 | 状态 |
| --- | --- | --- |
| T37 | 美股超跌反弹回测引擎修复与重跑 | 已合入 |
| T38 | 通知分发合并为 `send_with_results()` 单一实现 | 已合入 |
| T39 | 历史记录 → 报告对象三份合一（`report_builder`） | 已合入 |
| T40 | `ChatPage` 拆分 + 选择器订阅 + 消息记忆化 + 输入法 Enter 保护 | 已合入 |
| T41 | 配置单一声明源：以方案交付（Q1 继续跟上游），本地保留 T17 结构性测试 | 已合入（文档） |
| T42 | 常青文档：`docs/ai/` 五份文档 + `scripts/gen_ai_codemap.py --check` | 已合入 |
| T43 | 测试治理：`tests/support/pipeline_factory.py` 统一 pipeline 构造，清理旧常量断言 | 已合入 |
| T44 | 双语补齐（第一批）：选股页与模型渠道编辑器文案迁入 `uiText.ts` | 已合入 |
| T45 | 飞书同源 HTML 报告：历史报告可查看 / 下载自包含 HTML，与飞书卡片共用解析 | 已合入 |
| T77 | HTML 报告固定中文：渲染器写死 `zh`，历史 HTML 接口按 `zh` 重建报告，Web 端 HTML 入口文案固定中文 | 已完成（仅框架/标签层） |
| T78 | HTML 报告彻底中文化：en/ko 记录生成并缓存中文变体（字段覆盖表 / 大盘复盘 Markdown），HTML 接口 409 + Web 生成按钮，CLI 批量回填 | 已实现（针对性测试通过；全量离线回归中） |

### 2.1 上线审查任务书进度（`.claude/reviews/release-fix-tasks-20261115.md`）

- R2–R25 已合入。2026-11-15 上线验收发现部分任务未达到验收标准，已在后续提交中修复：配置写入的引号与 `${...}` 引用绕过（R2）、通知测试改目标地址后复用已存密钥（R3）、限速槽位过期与基本面限流饿死资金流（R4/R11）、首屏预加载 Markdown 块（R6）、断开后心跳不刷新与刷新后停止不可达（R7）、英文复数关键词（R8）、LLM 重试层与 Agent 链路吞掉取消（R13）、大盘摘要提取误判（R17）、收盘后复用盘中快照（R18）、技能观点评估版本号未升（R21）、完整性重试的真实异常未回落（R24）、引导卡取消未作废服务端草稿、auto-tag 缺写权限且未按标题决定升级级别（R29）。
- `main` 分支保护状态几经变更：2026-11-18 由所有者关闭；2026-11-22 重新启用（必需检查 `ai-governance`/`backend-gate`/`web-gate`/`docker-build`，管理员同样受限，PR 评审非必需）。因此直接 `git push origin main` 会被 protected branch hook 拒绝，需走短分支 + PR，必需检查通过后合并。合入前仍应本地跑 `./scripts/ci_gate.sh`。
- vcr_short 回测报告已按 AGENTS.md 第 10 节补齐（本机 `local/backtests/backtest_report_20261118.md`，不入库）：概述、策略逻辑、绩效、交易明细、风险分析、结论六节俱全；结论为负期望且样本仅 15 笔，股票池清单未随结果保存（只能推测来源），重跑时需一并保存标的清单。
- R26（日线成交量统一成“股”，含历史库迁移）已完成代码与离线测试。2026-11-15 联网复核 efinance 日线：沪市主板、深市主板、创业板的“原始成交量 × 100 ÷（成交额 ÷ 收盘价）”比值约为 1，确认以“手”计；科创板、北交所、ETF 当时因东财限流未能复测；2026-11-18 联网补测通过：688981（科创板）、430047（北交所）、510300（ETF）最近两个交易日同一比值为 0.98–1.01，均以“手”计，乘 100 换算对这些市场同样成立，R26 无遗留项。
- R27：基线（`f052eaba`）与本轮最终代码（`e0db4c08`）的全量测试已完成；最终 `pytest -m "not network"` 为 **9438 passed / 0 failed / 0 errors / 4 deselected**（非沙箱），分诊与新基线见 `.claude/reviews/test-triage-20261115.md`、`.claude/reviews/test-baseline-20261115.md`。
- R28：Instabox 运维文件已移出公开仓库到 `local/ops/`；`local/scratch/market_router` 已取消跟踪；扫描器 `data10` 路径已参数化。
- R29：`auto-tag.yml` 已改为只读提交标题第一行；`AGENTS.md` 第 8 节已补分支/PR/并发规则；提交 `8d68c6d77` 的任务映射见 `.claude/reviews/commit-8d68c6d77-task-map.md`。`main` 分支保护已于 2026-11-18 关闭（见上）。
- R30：语法/flake8/确定性检查、前端 lint/build/vitest/smoke、Playwright UI 用例 35/35、桌面端 51 个测试、静态资源与 AI 资产检查均通过；后端全量 0 失败。Docker daemon 与 GitHub CLI 凭据不可用，Docker 构建、推送和真实模型/行情手工项未执行，详见 `.claude/reviews/release-acceptance-20261115.md`。

原任务书中的历史任务状态：T5 未完成，见 R15；T16 未完成，见 R3；T20 未完成，见 R16；T11 未完成，见 R26。

## 3. 半成品与已知限制

| 项 | 状态 | 处理建议 |
| --- | --- | --- |
| 配置三处声明 | `config.py` / `config_registry.py` / `system_config_service.py` 仍需手工同步（101 个键同时出现在三处） | 方案见 `docs/config/config-single-source.md`；本地不再扩大同步面，新增键必须同时补齐 T17 测试与 `.env.example` |
| 本地 CLI 生成后端 | `codex_cli` / `claude_code_cli` / `opencode_cli` 标注 experimental | 按 Q3：不删、不再新增维护投入；故障按上游同步处理 |
| 微信仪表盘图片渲染 | 依赖 Pillow 与中文字体，缺字体时降级文本 | 排障见 `docs/alerts.md`、`docs/FAQ.md` |
| 前端双语文案 | 选股页与渠道编辑器已迁入 `uiText.ts`；扫描器整页、告警页、持仓页仍中英混排，`uiText.ts` 与 `report_language.py` 各维护一套 | 改文案时两边评估；新增页面文案只进 `uiText.ts`，旧页面按 T44 后续批次迁移 |
| 首问 v1 兼容路径 | `interaction_version=1` 的旧 questionnaire（`select_questions` / `_confirmation_question`）与 v2 并存一个版本，供缓存页面回退 | 下一个版本评估移除 `create_intake` 旧路径与 `_confirmation_question` |
| 测试治理 | 部分大测试文件数千行、跨层 mock | 新建 pipeline 测试统一用 `tests/support/pipeline_factory.make_pipeline()`；旧断言不再为现状拍照 |
| 登录限流 | 限流计数保存在进程内字典：重启清零，多 worker/多副本各自计数 | 当前部署模型为单进程；扩容到多 worker 前需改为共享存储（SQLite/Redis），详见 `docs/DEPLOY.md` |

## 4. 已移出主干

- `data_provider/market_router/`：未接线，已按 Q2 移出主干；将来需要分钟线时以 Fetcher 形式重新接入，不要恢复旧目录。
- 调度器「重启会杀分析进程」的旧行为：已改为只重建调度线程。

## 5. 不要大改的上游文件

这些文件与上游持续合并，改动应当是「最小、可解释、可回滚」的补丁，不要顺手重构：

| 文件 | 原因 |
| --- | --- |
| `src/config.py` | 3973 行，字段/装载/校验三合一；大改会与上游冲突 |
| `src/core/config_registry.py` | 5335 行，键元数据与 `.env` 示例文本耦合 |
| `src/services/system_config_service.py` | 5761 行，校验与写入语义密集 |
| `src/storage.py` | 5355 行，表结构与迁移历史多，改字段需要考虑存量数据 |
| `data_provider/base.py` | 5164 行，fallback 主逻辑，改顺序影响所有数据源 |
| `src/analyzer.py` | 6032 行，Prompt 与结构化解析耦合 |
| `src/llm/local_cli_backend.py` | 2966 行，上游 experimental 能力 |

**判断方法**：改动前先看 `docs/ai/CODEMAP.md` 的「引用」列，引用次数高的模块影响面大；再确认是否有对应测试覆盖。

## 6. 文档维护入口

| 文档 | 维护方式 |
| --- | --- |
| `docs/ai/CODEMAP.md` | 生成：`python scripts/gen_ai_codemap.py`；CI `--check` 校验 |
| `docs/ai/FLOWS.md` | 手写，链路变化时更新 |
| `docs/ai/DATA-FLOW.md` | 手写，数据源 / 字段 / 提示词变化时更新 |
| `docs/ai/PITFALLS.md` | 手写，踩坑后追加 |
| `docs/ai/STATUS.md` | 手写，每个阶段结束更新 |
