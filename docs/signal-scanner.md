# 交易信号扫描器集成

本模块由交易信号扫描器 0.2.0 的完整业务实现迁入，源文件及 SHA-256 见 [来源清单](signal-scanner-source-manifest.json)。后端位于 `src/services/signal_scanner/`，前端位于 `apps/dsa-web/src/components/scanner/`，测试位于 `tests/signal_scanner/`，离线工具位于 `scripts/signal_scanner/`。

## 入口和能力

DSA 左侧导航“信号扫描器”进入 `/scanner`。页面沿用 DSA 登录和主导航，包括今日工作台、信号与观察、日志与复盘、规则验证、设置与数据、全景大屏。API 统一挂载在 `/api/v1/scanner`。

信号详情的“带入证据继续 AI 研究”将证券、信号 ID、指定版本的触发条件、评分、冻结阈值和反方证据载入问股草稿。载入时校验证券和版本，保留缺失值，超长证据明确标记截取；用户编辑过的草稿不会被迟到响应覆盖。打开链接不会自动提交分析。

迁入能力覆盖 12 条规则、特征与评分、日线/周线扫描、状态演化、来源质量、市场环境、人工 STOP、计划与预算保留、纸面账户和账本、复盘报告、备份恢复、隐私导出、批量历史分析及隔离的规则研究验证。

账户仍属于扫描器的纸面/自报账户域，未与 DSA 持仓余额合并。原始规则事件仍独立于 DSA 的 AI 决策信号，避免相反建议失效机制破坏规则警告。所有研究和数据质量门禁保持：规则确认不是交易授权，EOD 诊断不是 RTH/PIT 验证，UNKNOWN 和未评分不会自动补齐。

## 启动与配置

安装更新后的 `requirements.txt`，构建 Web，正常启动 DSA 服务。应用管理扫描、研究以及可选行情采集进程，异常退出后自动恢复，关闭服务时同时停止其进程。所有模块复用 DSA 的管理员账号、密码与会话；退出登录使原会话在所有模块失效。

- `SCANNER_RUNTIME`：独立持久目录；未设置时为 `DATABASE_PATH` 同级的 `signal-scanner/`。
- `SCANNER_WORKERS_ENABLED`：默认 `true`。设为 `false` 可保留查询/API 并改为手动运行 worker；队列任务需要 worker 才能执行。
- `SCANNER_ORIGIN`：可选浏览器 Origin。正常同源部署无需设置；开发代理或特殊反向代理应设置为实际浏览器地址。写请求需要合法 Origin 和会话对应的 CSRF token。
- `SCANNER_LEGACY_HOST`：可选原扫描器域名，配合 `SCANNER_ORIGIN` 将旧网页跳转到统一入口；旧域名上的写请求明确拒绝，不转发旧密码。
- `SCANNER_DAILY_MARKET`：默认 `false`。开启后运行增量行情采集；还需要已配置的数据源、证券池和合法凭据。
- `TIINGO_API_KEY` / `TIINGO_API_KEYS`、`ALPHAVANTAGE_API_KEYS`、已有 `FINNHUB_API_KEY`、`ALPACA_API_KEY` / `ALPACA_SECRET_KEY`：相应行情/事件适配器的凭据；密钥池使用 JSON 数组，仅在私有配置中设置。
- `SCANNER_WEBHOOK_URL`：可选外部通知地址；域内偏好默认 `external_channel=NONE`，迁移不会自动启用推送。
- `RULE_VALIDATION_WEB_ENABLED`：默认 `true`。
- `RULE_VALIDATION_WEB_ROOT`：独立研究控制目录，默认在扫描运行目录下。
- `RULE_VALIDATION_INPUT_ROOTS`：允许登记的冻结输入目录，多个目录使用操作系统路径分隔符。
- `SCANNER_HTTPS`：仅影响保留的独立 CLI 服务会话；集成入口使用 DSA 会话设置。

新运行目录没有真实行情、账号余额或凭据。数据状态会如实显示缺失。迁移没有把原系统的 `runtime/`、大数据包、密码、cookie、会话或历史待发通知写入仓库。

这些启动、路径与私有凭据配置在 DSA 配置注册表中明确标为部署配置，不由通用 Web 设置页热更新。修改后重启服务；扫描器内的证券池、规则、偏好、风险设置仍使用其版本化设置接口。

## 离线与手动工具

以下命令在仓库根目录执行，使用已安装项目依赖的 Python。将路径参数替换为自己的独立目录。

```bash
python -m src.services.signal_scanner.cli --runtime /path/to/scanner ingest /path/to/market.json --activate
python -m src.services.signal_scanner.cli --runtime /path/to/scanner run DAILY_SCAN
python -m src.services.signal_scanner.research.cli fixture --output /path/to/synthetic-input --symbols 2 --sessions 126
python -m src.services.signal_scanner.research.cli validate /path/to/synthetic-input/manifest.json
python -m src.services.signal_scanner.research.cli run /path/to/synthetic-input/manifest.json --runtime /path/to/research-output
python -m src.services.signal_scanner.cli restore /path/to/backup.zip /path/to/new-runtime
```

`fixture` 明确生成虚构测试数据。离线回放输出按独立快照、代码及依赖版本封存，不能把工程测试结果解释为真实交易收益。

要在“规则验证”中新建实验，先把 `RULE_VALIDATION_INPUT_ROOTS` 设置为冻结输入的父目录，然后显式登记数据包。命令中的研究控制目录必须与 Web 使用的 `RULE_VALIDATION_WEB_ROOT` 一致；默认是 `SCANNER_RUNTIME/rule-validation`。

```bash
python -m src.services.signal_scanner.worker.rule_validation --root /path/to/scanner/rule-validation init
python -m src.services.signal_scanner.worker.rule_validation --root /path/to/scanner/rule-validation --input-root /path/to/frozen-inputs register-dataset /path/to/frozen-inputs/sample/manifest.json --name "冻结研究样本"
python -m src.services.signal_scanner.worker.rule_validation --root /path/to/scanner/rule-validation import-report --runtime /path/to/research-output --ref reports/REPORT_HASH/report.json --name "已有离线报告"
```

最后一个命令的 `--ref` 使用离线运行实际返回的 `report_ref`，不要猜测报告路径。复现依据包含迁入源码哈希、原依赖规格和当前 Python/关键依赖版本；封存后更改实现或环境会触发版本不匹配，需保留原封存环境或重新登记实验。

来源系统的机器专用发布、LaunchAgent 安装和启动脚本保存在 `docs/signal-scanner/upstream-tools/` 作为历史源码参考。当前集成使用 DSA 启动与打包脚本，不运行这些旧主机脚本。

来源根目录的批处理启动示例、旧前端测试/构建入口及专题研究辅助源码也保存在该参考目录；可用的批量历史业务实现仍由扫描器 API 和 worker 提供。`scripts/signal_scanner/` 中带历史日期的专题实验工具需要对应输入数据，不随 DSA 启动，也不把原历史实验视为新环境已完成的验证。完整来源清单包含 157 个文件，逐项保留原始哈希与当前去向。

## 验证与回滚

```bash
SCANNER_WORKERS_ENABLED=false python -m pytest tests/signal_scanner tests/test_signal_scanner_host.py
./scripts/ci_gate.sh
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

API 集成测试需要覆盖 DSA 登录、CSRF、幂等、动态路由、研究任务和写入隔离；来源回归测试覆盖规则、账户、STOP、预算、事件、回放与旧版本复现。截图属于临时验收证据，放在仓库之外。

## 历史数据迁移与域名切换

1. 停止原扫描、行情及研究服务，确认没有 `RUNNING` 作业；保留原运行目录和服务配置作为回滚来源。
2. 使用新目录运行迁移工具：

```bash
python scripts/signal_scanner/migrate_runtime.py --source /path/to/original/runtime --destination /path/to/dsa/data/signal-scanner
```

工具以 SQLite 备份接口读取数据库，执行完整性检查，逐文件核验 SHA-256，全部成功后再发布目标目录。已有目标目录不会被覆盖。保留行情、配置、信号、账户、账本、作业、导入文件及研究产物；不复制原登录密码、浏览器会话、进程锁和历史备份包。`migration.json` 记录核验清单，包含私有路径，只保存在运行目录。

3. 在 DSA 私有配置中设置新 `SCANNER_RUNTIME`，迁入已有数据源配置；行情采集状态沿用原系统。冻结研究输入通过 `RULE_VALIDATION_INPUT_ROOTS` 指向可用的只读来源，历史内容和哈希不能随意改写。
4. 设置 `SCANNER_ORIGIN` 为统一站点地址；需要接管旧域名时，同时设置 `SCANNER_LEGACY_HOST`，把旧域名反向代理指向 DSA。旧深层网页保留其信号/报告路径并进入统一登录流程。
5. 先备份 DSA 私有配置和数据库，再重启服务，核验登录、切换、退出、信号、行情、历史作业和新任务执行；最后停用原服务的自动启动，避免两套系统同时写入和重复采集。

## 发布验收与回滚

验证包含全仓库后端门禁、Web 完整测试及 lint/build、真实子进程恢复、迁移前后文件和数据库核验、正式域名登录与页面切换。具体发布结果以版本记录为准。源系统的风险和数据质量规则是业务能力的一部分；过期、缺失和未验证数据仍按原规则显示。

回滚时停止新版本扫描进程，恢复发布前私有配置和原域名代理配置，并重新启动保留的原服务；新运行目录与新增记录必须保留，不能用旧快照覆盖。若同时回退 DSA 代码，使用前一发布版本并重建网页，不删除数据目录。迁移副本与原数据各自独立，后续新增记录不能自动双向合并。

本说明与 [英文说明](signal-scanner_EN.md) 同步；来源清单保留全部 157 项原始文件的哈希和去向。
