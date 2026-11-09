/* eslint-disable react-refresh/only-export-components -- Shared upstream presentation helpers retain their public import contract. */
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { ratio, wallTime } from "../../api/research";
import type { Data } from "../../api/client";

const states: Record<string, string> = {
  QUEUED: "排队中",
  RUNNING: "运行中",
  CANCEL_REQUESTED: "正在取消计算",
  CANCELLED: "计算已取消",
  PASS: "快照核验一致",
  FAIL: "快照核验失败",
  VERSION_UNAVAILABLE: "冻结版本不可用",
  SUCCEEDED: "作业完成",
  PARTIAL: "部分完成",
  FAILED: "执行失败",
  SKIPPED: "已跳过",
  READY: "报告完整",
  NOT_READY: "报告未就绪",
  INDEXING: "正在构建索引",
  CORRUPT: "完整性异常",
  UNSUPPORTED_VERSION: "报告版本暂不支持",
  PENDING_REVIEW: "人工待评审",
  INSUFFICIENT: "完成样本不足",
  SUFFICIENT: "达到样本数要求",
  NO_SAMPLES: "无样本",
  POINT_IN_TIME: "逐时点历史",
  REVISED_HISTORY: "修订历史",
  EXPLORATORY: "探索性",
  CHRONOLOGICAL_60_20_20: "60/20/20 时序切分",
  COMPLETED: "完成",
  UNRESOLVED: "无法解决",
  EXCLUDED: "已排除",
  CONFIRMED: "已确认",
  INVALIDATED: "已失效",
  EXPIRED: "已到期",
};
const reasons: Record<string, string> = {
  RTH_UNVERIFIED: "数据包缺少常规交易时段证据，请补齐后重新登记。",
  FROZEN_INPUT_UNAVAILABLE:
    "该历史报告没有可核验的冻结行情输入，行情图不可用。",
  DATA_UNAVAILABLE_AT_CLOCK:
    "在所选时点没有可见的完整行情，不使用后来的修订补画。",
  LATEST_REVISION_INVALID_OR_GAP: "所选时点最新可见行情无效或中间缺少交易日。",
  EVENT_NOT_CONFIRMED: "此事件尚未确认，请选择触发时点。",
  EVENT_CLOCK_UNVERIFIED: "事件状态缺少对应业务时钟证据，行情图暂不可用。",
  NO_REGISTERED_VARIATIONS: "原实验没有预登记可用参数变化，不开放临时搜索。",
  VARIANT_SOURCE_UNAVAILABLE:
    "外部历史报告只读；请用已登记数据另建当前版本实验。",
  UNREGISTERED_PARAMETER: "变化不在原协议预登记范围内，已拒绝。",
  INVALID_SENSITIVITY: "只允许预登记的±20%有限值，整数参数必须保持整数。",
  RESEARCH_CANCELLED:
    "本次计算已取消，输入和已见登记保留；可对相同输入新建尝试。",
  CANCEL_CONFLICT: "作业已结束或尝试次数变化，请刷新后查看实际状态。",
  WORKER_CHILD_INTERRUPTED: "独立计算进程中断，恢复后可对相同输入重试。",
  POINT_IN_TIME_HISTORY_UNVERIFIED:
    "缺少逐时点历史证据，请补齐数据；不会自动改为修订历史。",
  POINT_IN_TIME_MEMBERSHIP_UNVERIFIED: "缺少历史证券池可知时间证据。",
  POINT_IN_TIME_CLASSIFICATION_UNVERIFIED: "缺少历史分类可知时间证据。",
  VERSION_UNAVAILABLE:
    "冻结配置与当前运行版本不符。可阅读历史报告，或复制后重新预检。",
  VALIDATION_STALE: "配置、输入、版本或登记域已变化，请重新预检。",
  INSUFFICIENT_WARMUP: "区间前的预热数据不足，请准备更长输入或缩短研究区间。",
  BASIS_MISMATCH: "数据包与表单的收益或复权口径不一致，请选择受支持的口径。",
  INVALID_DATE_RANGE: "请填写开始不晚于结束的有效交易日期。",
  INSUFFICIENT_FIVE_YEAR_RANGE: "此区间不足五年，只能作为探索性研究。",
  FIVE_YEAR_SPLIT_REQUIRED: "五年及以上区间必须使用60/20/20切分和隔离期。",
  REGISTRATION_CONFLICT:
    "此协议标识已对应另一份配置，请核对原协议或为新假设登记新标识。",
  SEALED_RANGE_ALREADY_REVEALED:
    "重叠封存区间已经被查看，不能再作为新的未见样本。",
  QUEUE_FULL: "研究队列已满，请等待空闲计算槽后重试。",
  WORKER_INTERRUPTED: "研究进程已中断，恢复专用服务后可对相同输入重试。",
  ARTIFACT_CORRUPT:
    "报告完整性校验失败，已停止展示；请维护人员检查原报告并重新登记。",
  INDEX_UNAVAILABLE: "报告索引不可用，请维护人员重新校验并登记。",
  INCOMPARABLE_EXPERIMENTS:
    "两个实验存在未声明的口径差异，请检查版本、时钟、截止时间、切分和样本阈值。",
  NO_AXIS_CHANGE: "所选比较轴没有变化，请选择具有实际单轴差异的实验。",
  NOT_MATURE: "尚未到达观察终点。",
  CROSS_SPLIT_LABEL: "标签窗口跨越研究切分边界，已按协议排除。",
};
export const reasonText = (code: string) =>
  reasons[code] || "该操作未完成，请根据错误详情修复数据或联系维护人员。";
export const statusText = (v: unknown, context = "job") =>
  v === "PENDING"
    ? context === "label"
      ? "未到观察终点"
      : "待确认"
    : v == null
      ? "—"
      : states[String(v)] || String(v);
export function Status({
  value,
  context,
}: {
  value: unknown;
  context?: string;
}) {
  return (
    <span
      className={`rv-status ${value === "FAILED" || value === "FAIL" || value === "CORRUPT" ? "rv-danger" : ""}`}
    >
      {statusText(value, context)}
    </span>
  );
}
export function Shell({
  title,
  subtitle,
  children,
  actions,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="rv">
      <div className="rv-breadcrumb">
        <Link to="/scanner/rule-validation">
          <FlaskConical size={16} />
          规则验证
        </Link>
        <span>/ 离线研究</span>
      </div>
      <div className="rv-heading">
        <div>
          <h1>{title}</h1>
          <p>{subtitle || "冻结输入 · 可追溯证据 · 人工研究评审"}</p>
        </div>
        <div className="rv-actions">{actions}</div>
      </div>
      {children}
    </div>
  );
}
export function Feedback({
  state,
}: {
  state: { loading: boolean; error: string; asOf?: string };
}) {
  return (
    <>
      {state.loading && (
        <p role="status" className="rv-note">
          正在读取研究记录…
        </p>
      )}
      {state.error && (
        <div role="alert" className="rv-notice rv-danger">
          {state.error}
          {state.asOf && (
            <p>保留截至 {wallTime(state.asOf)} 的结果，当前连接未更新。</p>
          )}
        </div>
      )}
    </>
  );
}
export function Json({
  value,
  label = "完整详情",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="rv-details">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="rv-empty">{children}</div>;
}
export function Panel({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="rv-panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
export function Table({
  columns,
  rows,
}: {
  columns: [string, string, ((row: Data) => ReactNode)?][];
  rows: Data[];
}) {
  const [sort, setSort] = useState<{ key: string; ascending: boolean } | null>(
    null,
  );
  const ordered = sort
    ? [...rows].sort((a, b) => {
        const value = (r: Data) =>
          (sort.key === "series"
            ? (r.series ?? r.value ?? r.horizon)
            : (r.net?.[sort.key] ?? r[sort.key])) ?? "";
        const av = value(a),
          bv = value(b);
        const difference =
          av !== "" &&
          bv !== "" &&
          Number.isFinite(Number(av)) &&
          Number.isFinite(Number(bv))
            ? Number(av) - Number(bv)
            : String(av).localeCompare(String(bv), "zh-CN");
        return sort.ascending ? difference : -difference;
      })
    : rows;
  return rows.length ? (
    <div
      className="rv-table-wrap"
      tabIndex={0}
      role="region"
      aria-label="可横向滚动的数据表"
    >
      <table>
        <thead>
          <tr>
            {columns.map(([key, label]) => (
              <th key={key} scope="col">
                <button
                  className="rv-sort"
                  aria-label={`${label}，当前表内排序`}
                  onClick={() =>
                    setSort((v) => ({
                      key,
                      ascending: v?.key === key ? !v.ascending : true,
                    }))
                  }
                >
                  {label}
                  {sort?.key === key ? (sort.ascending ? " ↑" : " ↓") : ""}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ordered.map((r, i) => (
            <tr key={r.id || r.event_id || i}>
              {columns.map(([key, , render]) => (
                <td key={key}>
                  {render ? (
                    render(r)
                  ) : r[key] == null ? (
                    "—"
                  ) : typeof r[key] === "object" ? (
                    <Json value={r[key]} label="查看" />
                  ) : (
                    String(r[key])
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty>当前条件没有记录。缺失结果不记作零。</Empty>
  );
}
export function Pager({
  cursor,
  next,
  onPage,
  limit = 50,
}: {
  cursor: number;
  next: number | null;
  onPage: (n: number) => void;
  limit?: number;
}) {
  return (
    <div className="rv-pager">
      <button
        disabled={!cursor}
        onClick={() => onPage(Math.max(0, cursor - limit))}
      >
        上一页
      </button>
      <span>第 {Math.floor(cursor / limit) + 1} 页</span>
      <button
        disabled={next == null}
        onClick={() => next != null && onPage(next)}
      >
        下一页
      </button>
    </div>
  );
}
export function Quantiles({
  rows,
  onSelect,
}: {
  rows: Data[];
  onSelect?: (row: Data) => void;
}) {
  const values = rows
    .flatMap((r) => [r.net?.p05, r.net?.p95, r.net?.mean])
    .filter((v) => v != null)
    .map(Number);
  const lo = Math.min(0, ...values),
    hi = Math.max(0.001, ...values),
    x = (v: unknown) => 160 + ((Number(v) - lo) / (hi - lo)) * 510;
  return (
    <>
      <p className="rv-note">
        端点为 p05 / p95，粗线为
        p25–p75，中线为中位数，圆点为均值。单位：收益百分比；并非最小值、最大值或置信区间。
      </p>
      <div
        className="rv-chart-scroll"
        tabIndex={0}
        role="region"
        aria-label="可横向滚动的分位图"
      >
        <svg
          className="rv-quantiles"
          viewBox={`0 0 740 ${Math.max(65, rows.length * 55 + 30)}`}
          role="img"
          aria-label="后续收益分位区间图，完整数字见下方表格"
        >
          <line
            x1={x(0)}
            x2={x(0)}
            y1="10"
            y2={rows.length * 55 + 10}
            stroke="#8a8f98"
            strokeDasharray="3 4"
          />
          {rows.map((r, i) => {
            const y = i * 55 + 30;
            return (
              <g key={i}>
                <text x="4" y={y + 5} fill="#d0d6e0" fontSize="14">
                  {r.series || r.value || `${r.horizon}会话`}
                </text>
                {r.net?.p05 != null ? (
                  <>
                    <line
                      x1={x(r.net.p05)}
                      x2={x(r.net.p95)}
                      y1={y}
                      y2={y}
                      stroke="#aeb4c0"
                      strokeWidth="2"
                    />
                    <line
                      x1={x(r.net.p25)}
                      x2={x(r.net.p75)}
                      y1={y}
                      y2={y}
                      stroke="#a4adff"
                      strokeWidth="9"
                    />
                    <line
                      x1={x(r.net.p50)}
                      x2={x(r.net.p50)}
                      y1={y - 9}
                      y2={y + 9}
                      stroke="#f7f8f8"
                      strokeWidth="2"
                    />
                    <circle cx={x(r.net.mean)} cy={y} r="4" fill="#f7f8f8" />
                  </>
                ) : (
                  <text x="170" y={y + 4} fill="#aeb4c0" fontSize="14">
                    — 无完成样本
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>
      <Table
        rows={rows}
        columns={[
          [
            "series",
            "口径",
            (r) =>
              onSelect ? (
                <button className="rv-sort" onClick={() => onSelect(r)}>
                  {r.value}
                  {r.rule_version ? ` · ${r.rule_version}` : ""} ↗
                </button>
              ) : (
                r.series || r.value || `${r.horizon}会话 / ${r.cost_tier}bp`
              ),
          ],
          ["completed", "完成数"],
          ["pending", "未到终点"],
          ["unresolved", "无法解决"],
          ["excluded", "排除"],
          ["status", "样本状态", (r) => <Status value={r.status} />],
          ["mean", "净均值", (r) => ratio(r.net?.mean)],
          ...["p05", "p25", "p50", "p75", "p95"].map(
            (k) =>
              [k, k, (r: Data) => ratio(r.net?.[k])] as [
                string,
                string,
                (r: Data) => ReactNode,
              ],
          ),
        ]}
      />
    </>
  );
}
export function Back({ to, label = "返回" }: { to: string; label?: string }) {
  return (
    <Link className="button" to={to}>
      <ArrowLeft size={16} />
      {label}
    </Link>
  );
}
