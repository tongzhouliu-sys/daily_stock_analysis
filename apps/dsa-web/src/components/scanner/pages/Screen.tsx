import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowLeft,
  ArrowUpRight,
  Clock3,
  Database,
  Expand,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import type { Data } from "../api/client";
import {
  Badge,
  dateTime,
  label,
  number,
  useData,
  scoreLabel,
  scoreNote,
} from "../components/ui";
import "../styles/screen.css";

const ruleNames: Record<string, string> = {
  "TR-01": "趋势突破",
  "TR-02": "均线交叉",
  "TR-03": "突破回踩",
  "VP-01": "放量突破",
  "VP-02": "低量突破",
  "VP-03": "供给压力",
  "VP-04": "量价背离",
  "VP-05": "量能衰退",
  "MO-01": "区间反弹",
  "MO-02": "MACD证据",
  "EV-01": "财报跳空",
  "RK-01": "波动冲击",
};
const sourceNames: Record<string, string> = {
  TIINGO: "SPY · 原始日线",
  FRED: "VIXCLS · 环境日值",
  FINNHUB: "EARNINGS · 财报日历",
};
const reasons: Record<string, string> = {
  CREDENTIAL_NOT_CONFIGURED: "等待服务端密钥",
  SOURCE_DATA_STALE: "尚未覆盖最新会话",
  SOURCE_UNREACHABLE: "来源暂时无法连接",
  SOURCE_PERMISSION_DENIED: "来源拒绝访问",
  NO_COVERAGE: "未获得覆盖确认",
  SOURCE_SESSION_SCOPE_UNVERIFIED: "RTH口径尚待核验",
  PARTIAL_COVERAGE: "仅覆盖部分对象",
};

function Block({
  title,
  eyebrow,
  extra,
  children,
  className = "",
}: {
  title: string;
  eyebrow: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={"sc-block " + className}>
      <div className="sc-block-head">
        <div>
          <span className="sc-eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
        {extra}
      </div>
      {children}
    </section>
  );
}

function Spark({
  values,
  color = "#9e9bff",
}: {
  values: number[];
  color?: string;
}) {
  if (values.length < 2) return <span className="sc-no-spark">暂无序列</span>;
  const lo = Math.min(...values),
    span = Math.max(...values) - lo || 1;
  return (
    <svg
      className="sc-spark"
      viewBox="0 0 140 30"
      role="img"
      aria-label="历史值缩略走势"
    >
      <polyline
        points={values
          .map(
            (v, i) =>
              `${(i / (values.length - 1)) * 140},${28 - ((v - lo) / span) * 25}`,
          )
          .join(" ")}
        stroke={color}
        fill="none"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function PriceChart({
  prices,
  features,
}: {
  prices: Data[];
  features: Data | null;
}) {
  const [range, setRange] = useState(252),
    [hover, setHover] = useState<number | null>(null);
  const rows = range ? prices.slice(-range) : prices;
  const values = rows.map((r) => Number(r.close));
  const last = rows[rows.length - 1],
    first = rows[0];
  const lo = Math.min(...values),
    hi = Math.max(...values),
    pad = (hi - lo) * 0.08 || 1,
    min = lo - pad,
    span = hi - lo + 2 * pad;
  const x = (i: number) => 60 + (i / Math.max(rows.length - 1, 1)) * 780;
  const y = (v: number) => 245 - ((v - min) / span) * 210;
  const line = values
    .map((v, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(v)}`)
    .join(" ");
  const volumes = rows.map((r) => Number(r.volume)),
    maxV = Math.max(...volumes) || 1;
  const selected = hover == null ? last : rows[hover];
  return (
    <>
      <div className="sc-chart-tools">
        <div>
          <strong>
            SPY <span>标普500 ETF</span>
          </strong>
          <p>
            {selected?.date || "尚无有效会话"} ·{" "}
            {selected
              ? "O " +
                number(selected.open) +
                "  H " +
                number(selected.high) +
                "  L " +
                number(selected.low) +
                "  C " +
                number(selected.close)
              : "等待已存来源数据"}
          </p>
        </div>
        <div className="sc-range">
          {[
            [60, "3M"],
            [252, "1Y"],
            [0, "ALL"],
          ].map(([v, t]) => (
            <button
              className={range === v ? "active" : ""}
              key={v}
              onClick={() => {
                setRange(Number(v));
                setHover(null);
              }}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      {!rows.length ? (
        <div className="sc-chart-empty">
          <Database size={28} />
          <p>等待可用日线</p>
          <small>未接入的数据不会被补成价格曲线</small>
        </div>
      ) : (
        <div className="sc-chart-area">
          <svg
            viewBox="0 0 900 320"
            role="img"
            aria-label={`SPY调整后日线，${first.date}至${last.date}，最后收盘${last.close}`}
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const sx = ((e.clientX - rect.left) / rect.width) * 900;
              setHover(
                Math.max(
                  0,
                  Math.min(
                    rows.length - 1,
                    Math.round(((sx - 60) / 780) * (rows.length - 1)),
                  ),
                ),
              );
            }}
            onMouseLeave={() => setHover(null)}
          >
            <defs>
              <linearGradient id="spy-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#8a86f4" stopOpacity=".3" />
                <stop offset="100%" stopColor="#8a86f4" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[0, 1, 2, 3, 4].map((i) => (
              <g key={i}>
                <line
                  x1="60"
                  x2="840"
                  y1={35 + i * 52.5}
                  y2={35 + i * 52.5}
                  stroke="#23252f"
                  strokeDasharray="3 6"
                />
                <text x="45" y={39 + i * 52.5} textAnchor="end">
                  {number(min + span - (i / 4) * span, 0)}
                </text>
              </g>
            ))}
            <path
              d={line + ` L${x(rows.length - 1)},250 L60,250 Z`}
              fill="url(#spy-fill)"
            />
            {features?.ma50 != null &&
              Number(features.ma50) >= min &&
              Number(features.ma50) <= min + span && (
                <g>
                  <line
                    x1="60"
                    x2="840"
                    y1={y(Number(features.ma50))}
                    y2={y(Number(features.ma50))}
                    stroke="#697182"
                    strokeDasharray="5 5"
                  />
                  <text x="845" y={y(Number(features.ma50)) - 6}>
                    MA50
                  </text>
                </g>
              )}
            <path
              className="sc-price-path"
              d={line}
              pathLength="1"
              fill="none"
              stroke="#a6a2ff"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            {rows.map((r, i) => (
              <rect
                key={r.date}
                x={x(i)}
                y={293 - (Number(r.volume) / maxV) * 28}
                width={Math.max(1, 760 / rows.length)}
                height={(Number(r.volume) / maxV) * 28}
                fill={Number(r.close) >= Number(r.open) ? "#506e6b" : "#71565e"}
                opacity=".65"
              />
            ))}
            <line x1="60" x2="840" y1="299" y2="299" stroke="#34343a" />
            {[0, Math.floor(rows.length / 2), rows.length - 1].map((i, j) => (
              <text
                key={j}
                x={x(i)}
                y="316"
                textAnchor={j === 0 ? "start" : j === 2 ? "end" : "middle"}
              >
                {rows[i].date}
              </text>
            ))}
            {hover != null && (
              <g>
                <line
                  x1={x(hover)}
                  x2={x(hover)}
                  y1="30"
                  y2="295"
                  stroke="#9697b1"
                  strokeDasharray="3 4"
                />
                <circle
                  cx={x(hover)}
                  cy={y(values[hover])}
                  r="4"
                  fill="#c3c0ff"
                  stroke="#15151f"
                  strokeWidth="2"
                />
              </g>
            )}
            <circle
              cx={x(rows.length - 1)}
              cy={y(Number(last.close))}
              r="3.5"
              fill="#c3c0ff"
            />
          </svg>
        </div>
      )}
      <div className="sc-indicators">
        {[
          ["MA20", features?.ma20],
          ["MA50", features?.ma50],
          ["RSI14", features?.rsi],
          ["ADX14", features?.adx],
          ["ATR14", features?.atr],
        ].map(([name, val]) => (
          <div key={String(name)}>
            <span>{name}</span>
            <b>{number(val)}</b>
          </div>
        ))}
      </div>
      <div className="sc-chart-note">
        公司行动调整后日线 / 原始股数成交量 · MA50为当前参考水平 · 仅供EOD研究
      </div>
    </>
  );
}

export default function Screen() {
  const [refresh, setRefresh] = useState(0),
    [clock, setClock] = useState(new Date()),
    [motion, setMotion] = useState(
      () => !matchMedia("(prefers-reduced-motion: reduce)").matches,
    );
  const result = useData("/api/screen", refresh, true),
    [fullError, setFullError] = useState("");
  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    const p = setInterval(() => {
      if (!document.hidden) setRefresh((v) => v + 1);
    }, 60000);
    return () => {
      clearInterval(t);
      clearInterval(p);
    };
  }, []);
  const d = result.data;
  const fred = d?.sources.find((s: Data) => s.source === "FRED"),
    tiingo = d?.sources.find((s: Data) => s.source === "TIINGO"),
    finnhub = d?.sources.find((s: Data) => s.source === "FINNHUB");
  const prices = d?.spy.prices || [],
    last = prices.at(-1),
    previous = prices.at(-2);
  const change =
    last && previous
      ? (Number(last.close) / Number(previous.close) - 1) * 100
      : null;
  const environment = d?.environment || { mode: "UNKNOWN" },
    coverage = d?.coverage || { ok: 0, expected: 0, failed: 0 };
  const issues = [
    ...(environment.mode === "UNKNOWN"
      ? [
          {
            title: "环境尚未形成同日结论",
            note: fred?.latest_market_date
              ? `VIX最近日期 ${fred.latest_market_date}；需与SPY完成会话对齐。`
              : "SPY或VIX输入尚未核验。",
            tag: "待核验",
          },
        ]
      : []),
    ...(tiingo?.rth_scope_verified === false
      ? [
          {
            title: "EOD口径待独立核对",
            note: "日线可用于研究，RTH与量口径未核验前不放开正式计划。",
            tag: "数据门",
          },
        ]
      : []),
    ...(finnhub?.complete_coverage_verified !== true
      ? [
          {
            title: "财报覆盖未完成",
            note:
              reasons[finnhub?.reason_code] ||
              "没有覆盖确认不能解释为无财报事件。",
            tag: "事件门",
          },
        ]
      : []),
    {
      title: d?.counts.accounts ? "账户需逐一核对" : "尚无已核对账户",
      note: "持仓、现金与资金流未知时，不能认定有可用预算。",
      tag: "账户门",
    },
  ];
  const time = (zone: string) =>
    clock.toLocaleTimeString("en-GB", { timeZone: zone, hour12: false });
  return (
    <div className={"screen-shell " + (motion ? "is-motion" : "")}>
      <div className="sc-atmosphere" aria-hidden="true" />
      <header className="sc-header">
        <div className="sc-brand">
          <span className="sc-brand-mark">
            <Activity size={25} />
          </span>
          <div>
            <span className="sc-eyebrow">
              SIGNAL SCANNER / RESEARCH TERMINAL
            </span>
            <h1>市场信号全景</h1>
            <p>先看事实，再审阅机会</p>
          </div>
        </div>
        <div className="sc-clock">
          <div>
            <span>新加坡 SGT</span>
            <strong>{time("Asia/Singapore")}</strong>
          </div>
          <div>
            <span>纽约 ET</span>
            <strong>{time("America/New_York")}</strong>
          </div>
          <small>
            {clock.toLocaleDateString("zh-CN", {
              timeZone: "Asia/Singapore",
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
              weekday: "short",
            })}
          </small>
        </div>
        <div className="sc-header-actions">
          <span
            className={"sc-read-state " + (result.error ? "has-error" : "")}
          >
            <i />
            {result.error
              ? "读取失败 · 保留旧快照"
              : result.loading
                ? "正在读取快照"
                : "已存快照 · 60s读取"}
          </span>
          <div>
            <button
              onClick={() => setRefresh((v) => v + 1)}
              aria-label="刷新大屏，仅刷新已存快照"
            >
              <RefreshCw size={17} />
            </button>
            <button
              onClick={() => setMotion((v) => !v)}
              aria-label={motion ? "关闭动态效果" : "开启动效"}
            >
              {motion ? <Pause size={17} /> : <Play size={17} />}
            </button>
            <button
              aria-label="进入全屏"
              onClick={async (e) => {
                try {
                  const shell = (e.currentTarget as HTMLElement).closest(
                    ".screen-shell",
                  ) as HTMLElement | null;
                  if (!shell) return;
                  if (document.fullscreenElement)
                    await document.exitFullscreen();
                  else await shell.requestFullscreen();
                } catch {
                  setFullError("浏览器未允许全屏，可最大化窗口查看。");
                }
              }}
            >
              <Expand size={17} />
            </button>
            <Link to="/scanner" aria-label="返回工作台">
              <ArrowLeft size={17} />
            </Link>
          </div>
        </div>
      </header>
      {fullError && <div className="sc-banner">{fullError}</div>}
      {result.error && (
        <div className="sc-banner danger">
          {result.error} · 本屏保留上次读到的数据；数据状态并未因此更新。
        </div>
      )}
      {d?.fixture_mode && (
        <div className="sc-banner">虚构验证环境 · 所有价格和记录仅用于测试</div>
      )}
      {!d && (
        <div className="sc-loading">
          <Activity />
          <p>{result.error || "正在装载可追溯的市场快照…"}</p>
        </div>
      )}
      {d && (
        <>
          <div className="sc-metrics">
            <div className="sc-stat">
              <span>
                本轮数据覆盖 <Database size={14} />
              </span>
              <strong>
                {coverage.expected ? `${coverage.ok}` : "—"}
                <em> / {coverage.expected || "未配置"}</em>
              </strong>
              <div className="sc-progress">
                <i
                  style={{
                    width: `${coverage.expected ? (coverage.ok / coverage.expected) * 100 : 0}%`,
                  }}
                />
              </div>
              <small>
                {coverage.failed
                  ? `${coverage.failed}个对象待处理`
                  : `${d.counts.bar_revisions}条行情修订 · 验证池 ${coverage.expected} 个`}
              </small>
            </div>
            <div className="sc-stat">
              <span>
                SPY 最新收盘 <span className="sc-mini-label">EOD</span>
              </span>
              <strong>
                {last ? number(last.close) : "—"}
                <em
                  className={
                    change == null ? "" : change >= 0 ? "positive" : "negative"
                  }
                >
                  {change == null
                    ? ""
                    : `${change >= 0 ? "+" : ""}${number(change)}%`}
                </em>
              </strong>
              <small>{last?.date || "暂无已核验日线"} · 公司行动调整基准</small>
            </div>
            <div className="sc-stat">
              <span>
                VIX 最近观测{" "}
                <Badge value={fred?.health.freshness || "UNKNOWN"} />
              </span>
              <strong>
                {fred?.latest_value || "—"}
                <em>指数点</em>
              </strong>
              <small>
                {fred?.latest_market_date || "暂无观测日期"} ·{" "}
                {fred?.health.freshness === "OK"
                  ? "历史日值"
                  : "非同日环境输入"}
              </small>
            </div>
            <div className="sc-stat">
              <span>
                研究信号 <span className="sc-mini-label">12 RULES</span>
              </span>
              <strong>
                {d.counts.research}
                <em>条</em>
              </strong>
              <small>研究分不是胜率 · 生命周期单独保留</small>
            </div>
            <div className="sc-stat">
              <span>
                正式计划资格 <ShieldCheck size={14} />
              </span>
              <strong>
                {d.counts.plan_allowed}
                <em>条</em>
              </strong>
              <small>成熟度、事件、STOP与预算仍需重检</small>
            </div>
            <div className="sc-stat">
              <span>
                账户风险状态{" "}
                <span className="sc-mini-label">SELF-REPORTED</span>
              </span>
              <strong className="sc-status-text">
                {d.risk.locks.length
                  ? "已锁定"
                  : d.counts.accounts
                    ? "需核对"
                    : "待核验"}
              </strong>
              <small>{d.counts.accounts}个已登记账户 · 无券商订单验证</small>
            </div>
          </div>
          <div className="sc-grid">
            <div className="sc-left">
              <Block
                title="数据链路"
                eyebrow="SOURCE HEALTH"
                extra={<span className="sc-counter">03 SOURCES</span>}
              >
                {d.sources.map((s: Data) => (
                  <div className="sc-source" key={s.source}>
                    <div className="sc-source-title">
                      <span className="sc-source-icon">
                        <Database size={15} />
                      </span>
                      <b>{s.source}</b>
                      <Badge value={s.status} />
                    </div>
                    <p>{sourceNames[s.source]}</p>
                    <div className="sc-health-grid">
                      {[
                        ["连接", s.health.connection],
                        ["权限", s.health.permission],
                        ["新鲜度", s.health.freshness],
                        ["质量", s.health.quality],
                      ].map(([k, v]) => (
                        <div key={k}>
                          <span>{k}</span>
                          <i
                            className={
                              [
                                "UP",
                                "GRANTED",
                                "OK",
                                "ROWS_VALID_SCOPE_UNVERIFIED",
                              ].includes(v)
                                ? "ok"
                                : v === "DISABLED"
                                  ? "off"
                                  : "warn"
                            }
                          />
                          <b>{label(v)}</b>
                        </div>
                      ))}
                    </div>
                    <small>
                      {s.latest_market_date
                        ? `数据日期 ${s.latest_market_date}`
                        : reasons[s.reason_code] || "等待覆盖确认"}
                    </small>
                  </div>
                ))}
              </Block>
              <Block
                title="环境与波动"
                eyebrow="MARKET REGIME"
                extra={<Badge value={environment.mode} />}
              >
                {" "}
                <div className="sc-regime">
                  <svg
                    viewBox="0 0 260 145"
                    role="img"
                    aria-label="当前环境状态，未知时不生成有效风险指针"
                  >
                    <path
                      d="M35 120 A95 95 0 0 1 225 120"
                      fill="none"
                      stroke="#272a36"
                      strokeWidth="10"
                      strokeLinecap="round"
                    />
                    <path
                      d="M35 120 A95 95 0 0 1 225 120"
                      fill="none"
                      stroke="#7872d0"
                      strokeWidth="10"
                      strokeLinecap="round"
                      strokeDasharray={
                        environment.mode === "UNKNOWN" ||
                        environment.vix == null
                          ? "0 300"
                          : `${(Math.min(50, Number(environment.vix)) / 50) * 298.45} 300`
                      }
                      opacity=".55"
                    />
                    <text x="28" y="143">
                      0
                    </text>
                    <text x="125" y="24">
                      25
                    </text>
                    <text x="227" y="143">
                      50
                    </text>
                    <text
                      x="130"
                      y="100"
                      className="sc-gauge-value"
                      textAnchor="middle"
                    >
                      {environment.mode === "UNKNOWN"
                        ? "—"
                        : number(environment.vix)}
                    </text>
                    <text x="130" y="124" textAnchor="middle">
                      {label(environment.mode)}
                    </text>
                  </svg>
                </div>
                <div className="sc-threshold">
                  <span>25 高压门槛</span>
                  <span>35 暂停新增</span>
                </div>
                <Spark
                  values={d.vix_history
                    .slice(-80)
                    .map((v: Data) => Number(v.value))}
                  color="#b5a280"
                />
                <p className="sc-copy">
                  {environment.last_aligned
                    ? `共同会话 ${environment.last_aligned.market_date}：${label(environment.last_aligned.mode)}，仅供历史参考。`
                    : "当前环境依据必须与SPY完成会话对齐。"}
                </p>
              </Block>
            </div>
            <div className="sc-center">
              <Block
                title="价格结构"
                eyebrow="PRICE & VOLUME"
                extra={
                  <span className="sc-counter">
                    {d.source_market_date || "WAITING FOR DATA"}
                  </span>
                }
                className="sc-price-block"
              >
                <PriceChart prices={prices} features={d.spy.features} />
              </Block>
              <Block
                title="规则观测矩阵"
                eyebrow="RULE OBSERVATORY"
                extra={
                  <span className="sc-counter">6方向 / 5警示 / 1证据</span>
                }
              >
                <div className="sc-rule-grid">
                  {d.rules.map((r: Data) => (
                    <div
                      className={"sc-rule " + (r.confirmed ? "triggered" : "")}
                      key={r.id}
                    >
                      <div>
                        <b>{r.id}</b>
                        <span>
                          {r.kind === "DIRECTION"
                            ? "方向"
                            : r.kind === "WARNING"
                              ? "警示"
                              : "证据"}
                        </span>
                      </div>
                      <p>{ruleNames[r.id]}</p>
                      <small>
                        {r.confirmed
                          ? `${r.confirmed} 已确认`
                          : r.pending
                            ? `${r.pending} 待确认`
                            : r.id === "EV-01" &&
                                finnhub?.publication_time_verified !== true
                              ? "事件待核验"
                              : "本快照无活跃事件"}
                      </small>
                      <i>{label(r.stage)}</i>
                    </div>
                  ))}
                </div>
              </Block>
              <Block
                title="当前研究卡片"
                eyebrow="SIGNAL EVIDENCE"
                extra={
                  <Link className="sc-sub-link" to="/scanner/signals">
                    进入审阅 <ArrowUpRight size={13} />
                  </Link>
                }
              >
                <div className="sc-signal-list">
                  {d.cards.length ? (
                    d.cards.slice(0, 2).map((c: Data) => (
                      <Link key={c.id} to={"/scanner/signals/" + c.id}>
                        <b>{c.ticker}</b>
                        <span>{c.family}</span>
                        <Badge value={c.status} />
                        <strong
                          aria-label={scoreNote({ ...c, quality_score: c.score })}
                        >
                          {scoreLabel({ ...c, quality_score: c.score })}
                        </strong>
                        <small>
                          {c.availability === "PLAN_ALLOWED"
                            ? "仍需重检预算"
                            : "仅研究 / 不可直接执行"}
                        </small>
                        <ArrowUpRight size={14} />
                      </Link>
                    ))
                  ) : (
                    <div className="sc-empty-inline">
                      当前快照没有活跃卡片。没有信号，不等于已排除风险。
                    </div>
                  )}
                </div>
              </Block>
            </div>
            <div className="sc-right">
              <Block
                title="审阅前置条件"
                eyebrow="DECISION GATES"
                extra={<ShieldCheck size={17} />}
              >
                <div className="sc-issues">
                  {issues.slice(0, 4).map((v, i) => (
                    <div key={v.title}>
                      <span className="sc-issue-n">0{i + 1}</span>
                      <div>
                        <b>{v.title}</b>
                        <p>{v.note}</p>
                      </div>
                      <small>{v.tag}</small>
                    </div>
                  ))}
                </div>
                <div className="sc-hard-stop">
                  订单执行：禁止 <span>研究与交易执行分离</span>
                </div>
              </Block>
              <Block
                title="已记录事件"
                eyebrow="EVENT LEDGER"
                extra={
                  <span className="sc-counter">
                    {d.counts.market_events} REVISIONS
                  </span>
                }
              >
                <div className="sc-event-list">
                  {d.events.length ? (
                    d.events.slice(0, 2).map((e: Data) => (
                      <div key={e.id}>
                        <i />
                        <div>
                          <span>
                            {e.date} <b>{e.ticker}</b>
                          </span>
                          <p>
                            {e.type === "DIVIDEND"
                              ? `除息事实 · USD ${e.payload.amount}`
                              : e.type === "SPLIT"
                                ? `拆股事实 · ${e.payload.ratio} 倍`
                                : `预计财报 · ${e.payload.hour || "UNKNOWN"}`}
                          </p>
                          <small>
                            {e.source} ·{" "}
                            {e.type === "EARNINGS"
                              ? "发布时间待核"
                              : "不代表现金已到账"}
                          </small>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="sc-empty-inline">
                      尚无已记录事件；覆盖未知不能解释为无事件。
                    </div>
                  )}
                </div>
              </Block>
              <Block
                title="风险协议"
                eyebrow="RISK POLICY"
                extra={<span className="sc-counter">USD / LONG</span>}
              >
                <div className="sc-policy">
                  {[
                    ["单笔风险", d.risk.policy.risk_pct],
                    ["日损限额", d.risk.policy.daily_loss_limit],
                    ["月损限额", d.risk.policy.monthly_loss_limit],
                    ["组合热度", d.risk.policy.portfolio_heat_limit],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <span>{k}</span>
                      <b>
                        {number(Number(v) * 100, 1)}
                        <em>%</em>
                      </b>
                    </div>
                  ))}
                </div>
                <small className="sc-policy-note">
                  协议阈值 ≠ 当前可用预算 · {d.risk.policy_version}
                </small>
              </Block>
            </div>
          </div>
          <section className="sc-operations">
            <div className="sc-ops-label">
              <Activity size={17} />
              <div>
                <b>运行轨迹</b>
                <span>RECENT OPERATIONS</span>
              </div>
            </div>
            {d.jobs.slice(0, 5).map((j: Data) => (
              <div className="sc-job" key={j.id}>
                <span>
                  <i className={j.status.toLowerCase()} />
                  {j.name.replace("SOURCE_", "")}
                </span>
                <small>{dateTime(j.created_at)}</small>
                <Badge value={j.status} />
              </div>
            ))}
          </section>
          <footer className="sc-footer">
            <span>
              <Clock3 size={13} /> 截至 {dateTime(d.data_as_of)} ·{" "}
              {d.data_mode || "尚无快照"}
            </span>
            <span>
              快照 {d.snapshot_id?.slice(-10) || "未发布"} · {d.scope_note}
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
