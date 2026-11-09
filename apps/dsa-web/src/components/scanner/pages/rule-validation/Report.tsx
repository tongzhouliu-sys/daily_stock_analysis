import { scannerApiPath } from "../../api/client";
import { useRef, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  base,
  mutateResearch,
  params,
  ratio,
  useResearch,
  wallTime,
} from "../../api/research";
import type { Data } from "../../api/client";
import Variants from "../../components/research/Variants";
import {
  Back,
  Feedback,
  Json,
  Pager,
  Panel,
  Quantiles,
  Shell,
  Status,
  Table,
} from "../../components/research/ui";
const tabs = [
  ["overview", "概览"],
  ["lifecycle", "生命周期"],
  ["distribution", "后续分布"],
  ["benchmarks", "基准"],
  ["groups", "分组"],
  ["protocol", "数据与协议"],
];
const rules = [
  "TR-01",
  "TR-02",
  "TR-03",
  "VP-01",
  "VP-02",
  "VP-03",
  "VP-04",
  "VP-05",
  "MO-01",
  "MO-02",
  "EV-01",
  "RK-01",
];
export default function Report() {
  const { runId } = useParams(),
    [search, setSearch] = useSearchParams(),
    tab = search.get("tab") || "overview",
    root = `/runs/${runId}`,
    state = useResearch(root + "/report"),
    run = useResearch(root),
    files = useResearch(root + "/artifacts"),
    nav = useNavigate();
  const [cursor, setCursor] = useState(0),
    [section, setSection] = useState("events"),
    [benchmarkKind, setBenchmarkKind] = useState("SPY"),
    [iid, setIid] = useState(""),
    [error, setError] = useState(""),
    [confirmation, setConfirmation] = useState(""),
    [reason, setReason] = useState(""),
    [busy, setBusy] = useState(false),
    key = useRef(crypto.randomUUID());
  const dimension = search.get("dimension") || "RULE";
  const r = state.data,
    f: Data = { ...(r?.defaults || {}) };
  for (const k of [
    "horizon",
    "cost_tier",
    "entry_basis",
    "split",
    "direction",
    "research_mode",
    "rule_id",
    "instrument_id",
    "year",
    "environment",
    "sector",
    "asset_type",
  ])
    if (search.has(k)) f[k] = search.get(k);
  const query = params(f),
    stats = useResearch(
      r
        ? root +
            `/statistics?${query}&dimension=${tab === "groups" ? dimension : "ECONOMIC_TOTAL"}`
        : null,
    );
  const b = useResearch(
    r && tab === "benchmarks"
      ? root +
          `/benchmarks?${params({ ...f, kind: benchmarkKind, ...(benchmarkKind === "UNCONDITIONAL" ? { instrument_id: iid } : {}) })}`
      : null,
  );
  const eventQuery =
    section === "outcomes" ||
    (section === "events" && search.get("event_scope") === "labels")
      ? f
      : {
          ...(f.rule_id ? { rule_id: f.rule_id } : {}),
          ...(f.instrument_id ? { instrument_id: f.instrument_id } : {}),
        };
  const rows = useResearch(
    r
      ? root +
          `/rows?${params({ section, cursor, event_scope: section === "events" ? search.get("event_scope") : undefined, ...eventQuery })}`
      : null,
  );
  const change = (k: string, v: string) => {
    const p = new URLSearchParams(search);
    if (v) p.set(k, v);
    else p.delete(k);
    setSearch(p);
    setCursor(0);
  };
  const horizon5 = useResearch(
      r && tab === "distribution"
        ? root + `/statistics?${params({ ...f, horizon: 5 })}`
        : null,
    ),
    horizon10 = useResearch(
      r && tab === "distribution"
        ? root + `/statistics?${params({ ...f, horizon: 10 })}`
        : null,
    ),
    horizon20 = useResearch(
      r && tab === "distribution"
        ? root + `/statistics?${params({ ...f, horizon: 20 })}`
        : null,
    );
  const cost5 = useResearch(
      r && tab === "distribution"
        ? root + `/statistics?${params({ ...f, cost_tier: 5 })}`
        : null,
    ),
    cost10 = useResearch(
      r && tab === "distribution"
        ? root + `/statistics?${params({ ...f, cost_tier: 10 })}`
        : null,
    ),
    cost20 = useResearch(
      r && tab === "distribution"
        ? root + `/statistics?${params({ ...f, cost_tier: 20 })}`
        : null,
    );
  const select = (k: string, label: string, opts: [string, string][]) => (
    <label>
      {label}
      <select
        value={String(f[k] || "")}
        onChange={(e) => change(k, e.target.value)}
      >
        {opts.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
  async function reveal() {
    setBusy(true);
    setError("");
    try {
      const result = await mutateResearch(
        root + "/reveal",
        {
          confirmation,
          reason,
          expected_registration_revision: run.data?.registration_revision,
        },
        key.current,
      );
      nav(`/scanner/rule-validation/runs/${result.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Shell
      title={run.data?.name || "可视化报告"}
      subtitle="以冻结报告为事实来源。所有筛选由服务器应用，缺失与零分别显示。"
      actions={
        <>
          <Back to={`/scanner/rule-validation/runs/${runId}`} label="运行详情" />
          <Link className="button" to={`/scanner/rule-validation/new?copy=${runId}`}>
            复制配置
          </Link>
        </>
      }
    >
      <Feedback state={state} />
      {r && (
        <>
          <div className="rv-notice">
            <strong>
              {r.data_kind === "SYNTHETIC"
                ? "SYNTHETIC · 合成工程样本"
                : "REAL · 真实数据，来源与证据须另核验"}
            </strong>
            <span>
              结果不代表规则有效或已批准。
              {r.identity.sealed_revealed
                ? "本报告包含已揭示段。"
                : "封存默认不揭示。"}
            </span>
          </div>
          <div className="rv-tabs" role="navigation" aria-label="报告页签">
            {tabs.map(([v, l]) => (
              <button
                key={v}
                aria-current={tab === v ? "page" : undefined}
                onClick={() => change("tab", v)}
              >
                {l}
              </button>
            ))}
          </div>
          <div className="rv-filters">
            {select("horizon", "观察期限", [
              ["5", "5 会话"],
              ["10", "10 会话"],
              ["20", "20 会话"],
            ])}
            {select("cost_tier", "单边滑点", [
              ["5", "5 bp"],
              ["10", "10 bp"],
              ["20", "20 bp"],
            ])}
            {select("entry_basis", "入场口径", [
              ["NEXT_AVAILABLE_OPEN", "下一可用开盘"],
              ["CONFIRM_CLOSE", "确认收盘"],
            ])}
            {select(
              "split",
              "研究集合",
              Object.keys(r.identity.split.sets)
                .filter((k) => k !== "SEALED" || r.identity.sealed_revealed)
                .map((k) => [k, k]),
            )}
            {select("direction", "方向", [
              ["LONG", "LONG"],
              ["BEARISH", "BEARISH（实际涨跌，不取反）"],
            ])}
            {select("rule_id", "规则筛选", [
              ["", "所有规则"],
              ...rules.map((x) => [x, x] as [string, string]),
            ])}
            <label>
              证券 ID
              <input
                value={f.instrument_id || ""}
                onChange={(e) => change("instrument_id", e.target.value)}
                placeholder="全部证券"
              />
            </label>
          </div>
          <Feedback state={stats} />
          {stats.data && (
            <p className="rv-note">
              当前完整筛选：{Object.values(stats.data.filters).join(" · ")}；
              {stats.data.denominators.economic_events} 独立经济事件，
              {stats.data.denominators.label_rows} 标签行。分位算法
              LINEAR_N_MINUS_ONE。
            </p>
          )}
          {tab === "overview" && (
            <>
              <div className="rv-metrics">
                <div>
                  <span>作业</span>
                  <Status value={run.data?.status} />
                </div>
                <div>
                  <span>报告完整性</span>
                  <Status value={run.data?.report_state} />
                </div>
                <div>
                  <span>当前口径样本</span>
                  <Status
                    value={stats.data?.items[0]?.status || "NO_SAMPLES"}
                  />
                </div>
                <div>
                  <span>研究评审</span>
                  <Status value={r.review.status} />
                </div>
              </div>
              <Panel title="研究切分与覆盖">
                <p>
                  交易日期：{r.identity.start_date} — {r.identity.end_date}
                  ；生成时间：{wallTime(r.generated_at)}
                </p>
                <div className="rv-split">
                  {Object.entries(r.identity.split.sets).map(([k, range]) => (
                    <div key={k}>
                      <strong>
                        {k === "SEALED" && !r.identity.sealed_revealed
                          ? "封存 · 未揭示"
                          : k}
                      </strong>
                      <span>{(range as string[]).join(" — ")}</span>
                    </div>
                  ))}
                </div>
                <p className="rv-note">
                  {r.identity.split.embargo.length
                    ? `隔离会话共 ${r.identity.split.embargo.length} 个；每个边界20会话。`
                    : "探索性研究没有封存块。"}
                </p>
                <div className="rv-metrics">
                  <div>
                    <span>预期证券</span>
                    <strong>{r.coverage.expected_instruments}</strong>
                  </div>
                  <div>
                    <span>可用证券</span>
                    <strong>{r.coverage.available_instruments}</strong>
                  </div>
                  <div>
                    <span>失败证券会话</span>
                    <strong>{r.coverage.failed_security_sessions}</strong>
                  </div>
                </div>
              </Panel>
              <Panel title="当前口径分布">
                <Quantiles rows={stats.data?.items || []} />
              </Panel>
              <Panel title="偏差与限制">
                {r.biases.concat(r.limitations).map((v: string, i: number) => (
                  <p key={i}>{v}</p>
                ))}
              </Panel>
            </>
          )}
          {tab === "lifecycle" && (
            <Panel title="12规则生命周期">
              <p className="rv-note">
                以下为全报告生命周期，独立于后续标签筛选。触发、确认、失效、到期、阻断可能重叠，不能加总成互斥漏斗。TR-01/VP-01
                保留双归因，经济总量去重；MO-02 不产生独立方向收益样本。
              </p>
              <div className="rv-bars">
                {r.lifecycle_counts.map((row: Data) => (
                  <Link
                    key={row.rule_id}
                    to={`?${params({ ...Object.fromEntries(search), rule_id: row.rule_id })}#events`}
                    className="rv-bar-row"
                  >
                    <span>{row.rule_id}</span>
                    <svg
                      viewBox="0 0 250 50"
                      role="img"
                      aria-label={`${row.rule_id} 触发${row.triggered} 确认${row.confirmed}`}
                    >
                      <rect
                        x="0"
                        y="5"
                        height="14"
                        width={Math.max(
                          0,
                          (row.triggered /
                            Math.max(
                              1,
                              ...r.lifecycle_counts.map(
                                (x: Data) => x.triggered,
                              ),
                            )) *
                            245,
                        )}
                        fill="#a4adff"
                      />
                      <rect
                        x="0"
                        y="28"
                        height="14"
                        width={Math.max(
                          0,
                          (row.confirmed /
                            Math.max(
                              1,
                              ...r.lifecycle_counts.map(
                                (x: Data) => x.triggered,
                              ),
                            )) *
                            245,
                        )}
                        fill="#d0d6e0"
                      />
                    </svg>
                    <span>
                      触发 {row.triggered} / 确认 {row.confirmed}
                    </span>
                  </Link>
                ))}
              </div>
              <Table
                rows={r.lifecycle_counts}
                columns={[
                  ["rule_id", "规则"],
                  ["triggered", "触发"],
                  ["confirmed", "确认"],
                  ["invalidated", "失效"],
                  ["expired", "到期"],
                  ["blocked", "阻断"],
                  ["evaluable_security_sessions", "可评估证券会话"],
                  ["not_evaluable", "不可评估"],
                  ["triggers_per_1000_sessions", "每千会话触发数"],
                ]}
              />
            </Panel>
          )}
          {tab === "distribution" && (
            <>
              <Panel title="5 / 10 / 20会话后续分布">
                <Feedback state={horizon5} />
                <Feedback state={horizon10} />
                <Feedback state={horizon20} />
                <Quantiles
                  rows={[horizon5, horizon10, horizon20].flatMap(
                    (s) => s.data?.items || [],
                  )}
                />
                <p className="rv-note">
                  已确认后失效的事件仍保留固定期限标签。BEARISH
                  记录标的实际涨跌；MO-01 为 TRACK 上限。
                </p>
              </Panel>
              <Panel title="成本敏感性">
                <p className="rv-note">
                  同一组、同期限、同入场及同集合。毛收益不随成本变化，净收益包含双边滑点及费用。
                </p>
                <Feedback state={cost5} />
                <Feedback state={cost10} />
                <Feedback state={cost20} />
                <Quantiles
                  rows={[cost5, cost10, cost20].flatMap((s) =>
                    (s.data?.items || []).map((x: Data) => ({
                      ...x,
                      series: `${x.cost_tier}bp`,
                    })),
                  )}
                />
                <Table
                  rows={[cost5, cost10, cost20].flatMap(
                    (s) => s.data?.items || [],
                  )}
                  columns={[
                    ["cost_tier", "单边bp"],
                    ["gross", "毛均值", (x) => ratio(x.gross.mean)],
                    ["net", "净均值", (x) => ratio(x.net.mean)],
                    ["completed", "完成数"],
                  ]}
                />
              </Panel>
            </>
          )}
          {tab === "benchmarks" && (
            <Panel title="基准分布">
              <div className="rv-filters">
                <label>
                  基准类型
                  <select
                    value={benchmarkKind}
                    onChange={(e) => setBenchmarkKind(e.target.value)}
                  >
                    <option value="SPY">SPY配对基准</option>
                    <option value="SECTOR">行业配对基准</option>
                    <option value="UNCONDITIONAL">单证券无条件基准</option>
                  </select>
                </label>
                {benchmarkKind === "UNCONDITIONAL" && (
                  <label>
                    无条件证券 ID
                    <input
                      value={iid}
                      onChange={(e) => setIid(e.target.value)}
                      placeholder="必须指定一个证券"
                    />
                  </label>
                )}
              </div>
              <Feedback state={b} />
              {b.data && (
                <>
                  <p className="rv-note">
                    {benchmarkKind === "UNCONDITIONAL"
                      ? "直接展示已登记的单证券会话摘要，不合并证券分位数，不作逐事件配对。"
                      : `信号完成 ${b.data.signal_completed}；成功配对 ${b.data.paired_count}；缺基准 ${b.data.missing_count}。信号与基准均使用相同完成交集，超额逐对计算。`}
                  </p>
                  <Quantiles rows={b.data.items} />
                </>
              )}
            </Panel>
          )}
          {tab === "groups" && (
            <Panel title="分组统计">
              <div className="rv-filters">
                <label>
                  分组维度
                  <select
                    value={dimension}
                    onChange={(e) => change("dimension", e.target.value)}
                  >
                    {[
                      ["RULE", "规则与版本"],
                      ["YEAR", "年份"],
                      ["ENVIRONMENT", "环境"],
                      ["SECTOR", "行业"],
                      ["ASSET_TYPE", "资产类型"],
                    ].map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                </label>
                {["year", "environment", "sector", "asset_type"].map((k) => (
                  <label key={k}>
                    {
                      (
                        {
                          year: "年份",
                          environment: "环境",
                          sector: "行业",
                          asset_type: "资产类型",
                        } as Data
                      )[k]
                    }
                    <input
                      value={f[k] || ""}
                      onChange={(e) => change(k, e.target.value)}
                      placeholder="全部"
                    />
                  </label>
                ))}
              </div>
              <p className="rv-note">
                联合条件从原始标签重新计算。规则组可重叠，不能加总为经济总量。
              </p>
              <Quantiles
                rows={stats.data?.items || []}
                onSelect={(row) => {
                  const p = new URLSearchParams(search);
                  p.set(
                    (
                      {
                        RULE: "rule_id",
                        YEAR: "year",
                        ENVIRONMENT: "environment",
                        SECTOR: "sector",
                        ASSET_TYPE: "asset_type",
                      } as Data
                    )[row.dimension],
                    row.value,
                  );
                  p.set("event_scope", "labels");
                  setSearch(p);
                  setSection("events");
                  setCursor(0);
                  document.getElementById("events")?.scrollIntoView();
                }}
              />
            </Panel>
          )}
          {tab === "protocol" && (
            <>
              <Panel title="数据质量">
                <Table
                  rows={Object.entries(r.coverage.failure_reasons).map(
                    ([reason, count]) => ({ reason, count }),
                  )}
                  columns={[
                    ["reason", "原因"],
                    ["count", "证券会话数"],
                  ]}
                />
                <p className="rv-note">
                  标签未到期、无法解决和排除状态分别显示在后续分布表。评估压缩区间保留起止与
                  count，不展开成虚构逐日记录。
                </p>
              </Panel>
              <Panel title="版本与登记">
                <p>
                  报告生成时：{r.identity.sealed_revealed ? "已揭示" : "未揭示"}
                  ；当前登记：
                  {run.data?.external
                    ? "外部历史报告 · 登记域未知"
                    : run.data?.current_registration_revealed
                      ? "已揭示"
                      : "尚未揭示"}
                  。
                </p>
                <p className="rv-note">
                  历史报告可读不等于当前代码可复跑；复制会创建新的实验身份并重新预检。
                </p>
                <Json value={r.identity} label="数据来源与版本详情" />
                <Json value={r.manifest} label="完整规范manifest" />
                {!run.data?.external &&
                  !r.identity.sealed_revealed &&
                  r.identity.split.sets.SEALED && (
                    <details className="rv-details">
                      <summary>明确揭示封存段</summary>
                      <div className="rv-notice">
                        揭示记录不可撤销。将创建参数与数据相同的派生执行，计算失败也不会恢复为未见样本。
                      </div>
                      <label>
                        确认文字
                        <input
                          value={confirmation}
                          onChange={(e) => setConfirmation(e.target.value)}
                          placeholder="REVEAL_SEALED"
                        />
                      </label>
                      <label>
                        揭示原因
                        <textarea
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                        />
                      </label>
                      {error && <p role="alert">{error}</p>}
                      <button
                        disabled={
                          busy || confirmation !== "REVEAL_SEALED" || !reason
                        }
                        onClick={reveal}
                      >
                        确认揭示并创建派生执行
                      </button>
                    </details>
                  )}
              </Panel>
            </>
          )}
          {tab === "protocol" && <Variants runId={runId!} />}
          <Panel title="报告附件">
            <Feedback state={files} />
            <div className="rv-downloads">
              {files.data?.items.map((a: Data) => (
                <a
                  key={a.name}
                  className="button"
                  href={scannerApiPath(`${base}${root}/artifacts/${a.name}`)}
                  download
                >
                  {a.name}{" "}
                  <span className="rv-note">
                    {Math.ceil(a.size_bytes / 1024)} KB
                  </span>
                </a>
              ))}
            </div>
            <p className="rv-note">
              下载为脱敏 Web
              派生物，附源业务hash。原报告保持不变；冻结输入及源码不通过此处下载。
            </p>
          </Panel>
          <section id="events">
            <Panel title="可追溯明细">
              <div className="rv-filters">
                <label>
                  记录类型
                  <select
                    value={section}
                    onChange={(e) => {
                      setSection(e.target.value);
                      setCursor(0);
                    }}
                  >
                    <option value="events">规则事件</option>
                    <option value="outcomes">后续标签（应用当前口径）</option>
                    <option value="assessments">评估压缩区间</option>
                    <option value="transitions">生命周期变更</option>
                  </select>
                </label>
              </div>
              <Feedback state={rows} />
              {rows.data && (
                <>
                  <p className="rv-note">
                    {rows.data.total} 条
                    {section === "outcomes" ? "标签记录，不是事件数" : "记录"}
                    。事件表按证券/规则筛选；强制收益口径仅应用于标签。
                  </p>
                  <Table
                    rows={rows.data.items}
                    columns={
                      section === "outcomes"
                        ? [
                            ["economic_event_id", "经济事件"],
                            ["instrument_id", "证券"],
                            ["horizon", "会话"],
                            ["cost_tier", "bp"],
                            ["entry_basis", "入场"],
                            [
                              "status",
                              "标签状态",
                              (x) => (
                                <Status value={x.status} context="label" />
                              ),
                            ],
                            [
                              "gross_return",
                              "毛收益",
                              (x) => ratio(x.gross_return),
                            ],
                            [
                              "net_return",
                              "净收益",
                              (x) => ratio(x.net_return),
                            ],
                            [
                              "reason_code",
                              "原因",
                              (x) => x.reason_code || x.excluded_reason || "—",
                            ],
                          ]
                        : section === "events"
                          ? [
                              ["rule_id", "规则"],
                              ["instrument_id", "证券"],
                              ["trigger_market_date", "触发交易日"],
                              ["direction", "方向"],
                              [
                                "lifecycle",
                                "状态",
                                (x) => <Status value={x.lifecycle} />,
                              ],
                              [
                                "event_id",
                                "证据",
                                (x) => (
                                  <Link
                                    className="button"
                                    to={`/scanner/rule-validation/runs/${runId}/events/${x.event_id}?${params({ ...Object.fromEntries(search), hash: r.report_hash })}`}
                                  >
                                    查看事件
                                  </Link>
                                ),
                              ],
                            ]
                          : section === "assessments"
                            ? [
                                ["instrument_id", "证券"],
                                ["rule_id", "规则"],
                                ["from_market_date", "区间起点"],
                                ["market_date", "区间终点"],
                                ["count", "会话权重"],
                                ["status", "状态"],
                                ["reason_code", "原因"],
                              ]
                            : [
                                ["event_id", "事件"],
                                ["market_date", "交易日"],
                                [
                                  "evaluated_at",
                                  "真实时刻",
                                  (x) => wallTime(x.evaluated_at),
                                ],
                                ["lifecycle", "状态"],
                                ["reason_code", "原因"],
                              ]
                    }
                  />
                  <Pager
                    cursor={cursor}
                    next={rows.data.next_cursor}
                    onPage={setCursor}
                  />
                </>
              )}
            </Panel>
          </section>
          <p className="rv-footnote">
            来源业务 hash：{r.report_hash} · 查询协议 web-stats-1
          </p>
        </>
      )}
    </Shell>
  );
}
