/* eslint-disable react-refresh/only-export-components -- Shared upstream presentation helpers retain their public import contract. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertCircle,
  ArrowUpRight,
  CircleCheck,
  Clock3,
  Database,
  LoaderCircle,
} from "lucide-react";
import { api, displayTimezone, type Data } from "../api/client";

export const labels: Record<string, string> = {
  NORMAL: "正常",
  STRESS: "高压",
  PANIC: "暂停新增",
  DEFENSIVE: "防守",
  UNKNOWN: "待核验",
  UNVERIFIED: "待核对",
  DISABLED: "未启用",
  CONFIRMED: "已确认",
  PENDING: "待确认",
  INVALIDATED: "已失效",
  EXPIRED: "已到期",
  RETRACTED: "已撤回",
  RESEARCH: "研究",
  SHADOW: "纸面观察",
  APPROVED: "已批准",
  HIGH: "重点",
  TRACK: "跟踪",
  WATCH: "观察",
  PLAN_ALLOWED: "可进入计划校验",
  OBSERVE: "仅观察",
  BLOCKED: "暂不可用",
  COMPLETE: "完整",
  INCOMPLETE: "不完整",
  COMPANION: "辅助",
  RTH: "常规交易时段",
  PRE: "盘前",
  POST: "盘后",
  CLOSED: "休市",
  OVERNIGHT: "隔夜",
  PAPER: "纸面",
  MANUAL: "自报实盘",
  USER_PLAN: "用户计划",
  SHADOW_EXPERIMENT: "研究实验",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  PARTIAL: "部分完成",
  QUEUED: "等待处理",
  RUNNING: "运行中",
  SKIPPED: "已跳过",
  RESERVED: "已保留预算",
  DRAFT: "草稿",
  WAIT: "等待",
  IGNORE: "忽略",
  PLAN_SAVED: "保存计划",
  NO_KNOWN_ISSUE: "未发现已知问题",
  KNOWN_RISK: "已知风险",
  CONFIRMED_EMPTY: "已核对无持仓",
  HAS_RECORDED_POSITIONS: "有已登记持仓",
  NO_SAMPLES: "暂无样本",
  CAUTION: "风险提醒",
  OK: "正常",
  PASS: "通过",
  WARN: "需复核",
  NOT_CHECKED: "尚未核验",
  SUPPORTED: "支持",
  UNSUPPORTED: "不支持",
  GRANTED: "已获权",
  DENIED: "未获权",
  ENABLED: "已启用",
  UP: "连通",
  STALE: "已过时",
  ACCEPTED: "平台已接收",
  ROWS_VALID_SCOPE_UNVERIFIED: "行值合格 / 口径待核",
  VALID_WITH_GAPS: "存在缺测",
  PARTIAL_COVERAGE: "部分覆盖",
  OBSERVED_EVENTS_ONLY: "仅已知事件",
  DEGRADED: "部分异常",
};
export const label = (v: unknown) =>
  v == null ? "—" : labels[String(v)] || String(v);
const scoreInputs: Record<string, string> = {
  sector_mapping: "行业分类",
  sector_reference: "行业ETF行情",
  market_reference: "市场基准行情",
  relative_strength: "行业相对强度",
  comparators: "两个比较ETF的配置或行情",
  participation: "板块参与度",
  ma10: "10日均线",
  ma20: "20日均线",
  ma50: "50日均线",
  adx: "ADX",
  di_plus: "+DI",
  di_minus: "−DI",
  rv: "量比",
  weekly: "完整周线",
};
export function scoreLabel(c: Data) {
  if (c.quality_score != null) return String(c.quality_score);
  return c.kind === "WARNING" || c.severity === "CAUTION"
    ? "不适用"
    : "待补数据";
}
export function scoreNote(c: Data) {
  if (c.quality_score != null) return "非胜率";
  if (c.kind === "WARNING" || c.severity === "CAUTION")
    return "风险提醒不参与质量评分";
  const missing: string[] = c.context?.score_missing_inputs || [];
  return missing.length
    ? "缺少：" + missing.map((x) => scoreInputs[x] || x).join("、")
    : "评分依赖不完整，需补齐后重新扫描";
}
export const number = (v: unknown, digits = 2) =>
  v == null
    ? "—"
    : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: digits });
export const dateTime = (v: unknown) =>
  v
    ? new Date(String(v)).toLocaleString("zh-CN", {
        timeZone: displayTimezone,
        hour12: false,
      }) +
      (displayTimezone === "Asia/Singapore" ? " SGT" : " " + displayTimezone)
    : "暂无数据";

export function useData(path: string | null, refresh = 0, keepPrevious = false) {
  const [result, setResult] = useState<{path: string | null; refresh: number; data: Data | null; error: string | null} | null>(null);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    api(path, "GET", undefined, undefined, controller.signal).then(
      data => { if (!controller.signal.aborted) setResult({path, refresh, data, error: null}); },
      error => { if (!controller.signal.aborted) setResult(old => ({path, refresh, data: keepPrevious && old?.path === path ? old.data : null, error: error.message})); },
    );
    return () => controller.abort();
  }, [path, refresh, keepPrevious]);
  const current = result?.path === path && result?.refresh === refresh;
  return {
    data: path && (current || (keepPrevious && result?.path === path)) ? result?.data ?? null : null,
    error: current ? result?.error ?? null : null,
    loading: !!path && !current,
  };
}
export function Badge({ value }: { value: unknown }) {
  return (
    <span className={"badge tone-" + String(value).toLowerCase()}>
      {label(value)}
    </span>
  );
}
export function Panel({
  title,
  children,
  action,
  className = "",
}: {
  title?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={"panel " + className}>
      {title && (
        <div className="panel-heading">
          <h2>{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function State({
  loading,
  error,
}: {
  loading?: boolean;
  error?: string | null;
}) {
  return loading ? (
    <div className="state">
      <LoaderCircle size={20} className="spin" /> 正在读取已保存的数据…
    </div>
  ) : error ? (
    <div className="notice danger">
      <AlertCircle size={18} />
      <span>{error}。请检查登录状态和数据状态，刷新不会重复提交操作。</span>
    </div>
  ) : null;
}
export function Empty({
  title,
  children,
  to,
}: {
  title: string;
  children: React.ReactNode;
  to?: string;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Database size={24} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {to && (
        <Link className="button secondary" to={to}>
          配置与数据状态 <ArrowUpRight size={16} />
        </Link>
      )}
    </div>
  );
}
export function Message({ text }: { text: string | null }) {
  return text ? (
    <div className="notice" role="status">
      <CircleCheck size={18} />
      <span>{text}</span>
    </div>
  ) : null;
}
export function SessionStrip({
  context,
}: {
  context: Data | null | undefined;
}) {
  if (!context) return null;
  return (
    <div className="session-strip">
      <span>
        <Clock3 size={15} />
        <Badge value={context.session_status} />
      </span>
      <span>
        下次开盘 <b>{dateTime(context.next_rth_open?.utc)}</b>
      </span>
      <span>
        事件覆盖 <Badge value={context.event_coverage?.status || "UNKNOWN"} />
      </span>
      {context.dst_notice && (
        <span className="warning">临近美东夏令时切换，请核对本地开盘时间</span>
      )}
    </div>
  );
}
export function Metric({
  name,
  value,
  note,
}: {
  name: string;
  value: React.ReactNode;
  note: string;
}) {
  return (
    <div className="metric">
      <span>{name}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}
export function SignalList({ items }: { items: Data[] }) {
  return items.length ? (
    <div className="table-wrap signal-table">
      <table>
        <thead>
          <tr>
            <th>标的 / 依据</th>
            <th>研究等级</th>
            <th>质量分</th>
            <th>状态</th>
            <th>距入场锚点</th>
            <th>数据截至</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.id}>
              <td>
                <Link className="symbol" to={"/scanner/signals/" + c.id}>
                  {c.instrument.ticker}
                </Link>
                <small>
                  {c.primary_rule} · {label(c.rule_stage)}
                </small>
              </td>
              <td>
                <Badge value={c.effective_grade || c.severity} />
              </td>
              <td className="numeric">
                {scoreLabel(c)}
                <small>{scoreNote(c)}</small>
              </td>
              <td>
                <Badge value={c.lifecycle} />
                <small>{label(c.availability)}</small>
              </td>
              <td className="numeric">
                {c.distance_to_entry_pct == null
                  ? "—"
                  : number(c.distance_to_entry_pct) + "%"}
                <small>
                  {c.distance_to_entry_atr == null
                    ? "ATR不可用"
                    : number(c.distance_to_entry_atr) + " ATR"}
                </small>
              </td>
              <td className="time">{dateTime(c.data_as_of)}</td>
              <td>
                <Link
                  aria-label={"查看" + c.instrument.ticker + "详情"}
                  to={"/scanner/signals/" + c.id}
                >
                  <ArrowUpRight size={18} />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty title="本轮暂无符合筛选的候选">
      有数据且没有信号，与数据尚未接通是两种状态。请同时查看覆盖率及失败原因。
    </Empty>
  );
}
