# PITFALLS · 环境约束与踩过的坑

> 手写维护。踩到新坑就追加一条，写清「现象 → 原因 → 做法」，不要只写结论。

## 环境

1. **Python 3.11 + `.venv`**：CI 与本地都按 3.11 跑。用 `source .venv/bin/activate` 后再 `pip install -r requirements.txt`，不要在系统 Python 上装依赖。
2. **不要在本地跑全量测试**：默认用 `python -m pytest -m "not network"` 做定向验证，一次改动的目标文件测试即可。全量留给 CI 或维护者显式要求。
3. **`scripts/ci_gate.sh` 是后端的准入门槛**：flake8 + 关键 pytest + `py_compile`，本地提交前优先跑它。
4. **网络测试是观测项**：`pytest -m network` 与 `scripts/test.sh quick` 在 `network-smoke.yml` 里跑，失败不阻断 PR，但需要看趋势。

## 数据

5. **本机回测的数据和结果禁止入库**：`local/data/`（含 `data10/` 与转换后的输入）、`local/backtests/`（报告、净值、交易明细）、`local/scratch/`（日志与临时产物）都只在本机使用，已在 `.gitignore` 里；不要用 `git add -f` 绕过，也不要把回测报告或数据作为 PR 文件提交。回测输入只通过 `scripts/signal_scanner/build_rebound_input.py` 生成。
6. **盘中当日 K 线是累计值**：抓到的当日 K 线未收盘，落库前必须裁掉，否则「今日已有数据」会挡住收盘后的同日重跑。pipeline 取数、agent 历史预取、`history_loader` 回写三处都要处理。
7. **回退到旧数据必须标注**：日线落后时报告写「截至 X（落后 N 个交易日）」，数据块降级为 `stale`，不能把多日前的行情当今日行情。
8. **成交量单位**：不同源返回「手」或「股」，统一在 fetcher 层归一，不要在分析层做二次换算。

## 报告与通知

9. **单渠道失败不能拖垮主流程**：`NotificationService.send_with_results()` 逐渠道隔离，失败写诊断快照（`record_notification_run`）后继续。
10. **微信与其他渠道内容不同**：微信走仪表盘/简报渲染，邮件按 `stock_email_groups` 分组，分组之间独立失败。
11. **图片海报失败要降级**：Markdown 转图片失败时继续发文本，不要整体判失败。
12. **改 `EXTRACT_PROMPT` 必须在 PR 里附完整最新 prompt**（`src/services/image_stock_extractor.py`）。
13. **HTML 报告模板必须放 `src/services/report_html/`**：Docker 镜像（`docker/Dockerfile`）只 COPY 根目录 `*.py` 与 `api/`/`data_provider/`/`bot/`/`src/`/`strategies/`，不包含根目录 `templates/`。HTML 渲染不能依赖 `REPORT_TEMPLATES_DIR`，否则容器里渲染失败。
14. **HTML 报告与飞书卡片共用飞书解析**：改 `feishu_card.py` 的章节解析 / 字段布局 / 脚注去重 / 主题映射时，必须同步跑 `tests/test_report_html_renderer.py`，否则 HTML 与飞书信息漂移。Jinja2 里字典的 `.items` 会被解析成 `dict.items()` 方法，模板中用 `block['items']` 下标访问；dataclass（`ReportDocument`）用属性访问。
15. **HTML 报告固定中文**：不要给 `render_report_html()` 重新加回 `report_language` 参数，接口层也不要把记录的 `report_language` 透传进去；历史 HTML 接口固定按 `zh` 重建报告。改语言行为需同步 `docs/report-html.md` 与 `docs/tasks/report-html-chinese-only.md`。

## 配置

13. **配置有 5–7 处同步点**：`src/config.py`（字段/默认值/`_load_from_env`）、`config_registry.py`（元数据/范围）、`system_config_service.py`（键清单/校验/兜底）、`.env.example`、文档。改默认值务必全局搜一遍字面量，见 `docs/config-single-source.md`。
14. **新增配置要「不配置也能跑」**：默认值必须让旧部署行为不变；同时更新 `.env.example` 与相关文档。
15. **配置写入有版本冲突**：`PUT /api/v1/system-config/config` 带 `config_version`，并发保存返回 409，前端要提示重新加载而不是静默覆盖。

## 发布与工作流

16. **自动 tag 是 opt-in**：只有 commit title 含 `#patch`/`#minor`/`#major` 才触发版本号更新。
17. **`static/` 是构建产物**：前端构建输出到仓库根 `static/`，已在 `.gitignore` 里，不要提交。
18. **桌面端要先构建 Web 再打包**：`apps/dsa-web` → `apps/dsa-desktop`，顺序反了会打进去旧前端。

## 前端

19. **`ChatPage` 的测试 mock 是「每次调用一个 useState」的假 store**：新增 store 字段读取时要跟着更新 `src/pages/__tests__/ChatPage.test.tsx` 的 `mockStoreState`，否则组件读到的是 undefined。
20. **打字不能重渲染历史消息**：消息项是 `memo` 的，父组件传给它的回调必须是稳定引用（用 ref 包一层），否则记忆化失效。
21. **Enter 发送要判断输入法**：`event.nativeEvent.isComposing || event.keyCode === 229` 时必须直接返回，否则中文候选词确认会误发送。
22. **双语文案**：`apps/dsa-web/src/i18n/uiText.ts` 与 `src/report_language.py` 各有中英文案，改一处要评估另一处。
23. **大页面拆分优先做「组件边界 + 稳定回调」**：直接拆文件但把不稳定回调传下去，性能反而更差。

## AI 协作资产

24. **`AGENTS.md` 是唯一真源**：`CLAUDE.md` 是软链接，`.github/copilot-instructions.md` 与 `.github/instructions/` 是镜像；改治理规则后跑 `python scripts/check_ai_assets.py`。
25. **`docs/ai/CODEMAP.md` 是生成文件**：手改会在 CI 的 `--check` 里失败，改完源码跑 `python scripts/gen_ai_codemap.py`。

## 测试与日志

26. **离线测试守卫**：`tests/support/network_guard.py` 默认以 `enforce` 模式拦截未标记 `@pytest.mark.network` 的外网连接（含子进程，靠 `DSA_OFFLINE_GUARD` 传递）；需要真实网络时用 `DSA_ALLOW_NETWORK=1` 或 `DSA_OFFLINE_GUARD=off/warn`。全量 `-m "not network"` 已在该模式下通过，新增联网用例必须显式标记。
27. **日志用 `%s` 惰性格式化**：`logger.info(f"...")` 关闭级别也会先拼字符串；外部文本（新闻标题、模型输出、URL）进日志前要清洗换行，避免日志注入。
28. **时间字段有两套约定**：`src/storage.py` 同时存在本地 naive 与 UTC naive，新增表必须在 `tests/test_time_basis_contract.py` 登记，并同步 `docs/ai/DATA-FLOW.md`。
29. **文档与实现一致性**：改配置键、接口上限、能力开关时，同时检查 `.env.example`、`config_registry`、接口 description、专题文档和 CHANGELOG；不要出现“文档已宣称、代码未实现”或“CHANGELOG 已删除、文件仍在”。
30. **全量离线套件成本**：约 12 分钟、峰值内存约 2.2GB；本地改动优先跑目标文件测试，全量留给 M0 收尾或最终验收。
