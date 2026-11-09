import { useParams, useSearchParams } from "react-router-dom";
import { ratio, useResearch, wallTime } from "../../api/research";
import type { Data } from "../../api/client";
import FrozenChart from "../../components/research/FrozenChart";
import {
  Back,
  Feedback,
  Json,
  Panel,
  Shell,
  Status,
  Table,
} from "../../components/research/ui";
export default function Event() {
  const { runId, eventId } = useParams(),
    [search] = useSearchParams(),
    state = useResearch(`/runs/${runId}/events/${eventId}`),
    d = state.data,
    e = d?.event;
  const mismatch = !!(
    d &&
    search.get("hash") &&
    search.get("hash") !== d.report_hash
  );
  return (
    <Shell
      title={e ? `${e.rule_id} · ${e.instrument_id}` : "事件证据"}
      subtitle="触发、确认与失效按真实业务时间记录。交易日保留原日期。"
      actions={
        <Back
          to={`/scanner/rule-validation/runs/${runId}/report?${search.toString()}#events`}
          label="返回报告与筛选"
        />
      }
    >
      <Feedback state={state} />
      {mismatch && <p role="alert">来源报告身份已变化，停止展示。</p>}
      {e && !mismatch && (
        <>
          <div className="rv-metrics">
            <div>
              <span>生命周期</span>
              <Status value={e.lifecycle} />
            </div>
            <div>
              <span>触发交易日</span>
              <strong>{e.trigger_market_date}</strong>
            </div>
            <div>
              <span>确认交易日</span>
              <strong>{e.confirmation_market_date || "—"}</strong>
            </div>
            <div>
              <span>方向 / 分级上限</span>
              <strong>
                {e.direction} / {e.effective_grade_cap || "—"}
              </strong>
            </div>
          </div>
          <Panel title="事件时间轴">
            <ol className="rv-timeline">
              <li>
                <strong>触发</strong>
                <span>{wallTime(e.triggered_at)}</span>
                <code>{e.triggered_at}</code>
              </li>
              {e.confirmed_at && (
                <li>
                  <strong>实际确认</strong>
                  <span>{wallTime(e.confirmed_at)}</span>
                  <code>{e.confirmed_at}</code>
                </li>
              )}
              {d.transitions.map((t: Data, i: number) => (
                <li key={i}>
                  <strong>
                    {t.market_date} · <Status value={t.lifecycle} />
                  </strong>
                  <span>{wallTime(t.evaluated_at)}</span>
                  <span>{t.reason_code || t.reason || "—"}</span>
                </li>
              ))}
            </ol>
            <p className="rv-note">
              确认时刻不回填至触发日。显示时间使用工作台偏好时区，UTC
              原值同时保留。
            </p>
          </Panel>
          <FrozenChart
            runId={runId!}
            eventId={eventId!}
            hash={d.report_hash}
            confirmed={!!e.confirmed_at}
          />
          <Panel title="冻结阈值与单位换算">
            <p className="rv-note">
              没有单位换算时，当前冻结值即原始冻结值；未记录的原始字段显示“—”。
            </p>
            <Table
              rows={Object.entries(e.frozen || {}).map(([k, v]) => ({
                field: k,
                frozen: v,
                original: e.original_frozen
                  ? (e.original_frozen[k] ?? null)
                  : v,
                unit:
                  k === "parent_event_id"
                    ? "事件 ID"
                    : k === "price_factor"
                      ? "无量纲因子"
                      : /close|open|high|low|atr|level|anchor/.test(k)
                        ? "USD（报告价格口径）"
                        : "见原始证据字段定义",
              }))}
              columns={[
                ["field", "字段"],
                ["unit", "单位"],
                ["original", "原始冻结值"],
                ["frozen", "当前换算值"],
              ]}
            />
            <Json
              label="单位换算依据"
              value={e.unit_conversions || { reason: "没有已登记的单位换算" }}
            />
            <Json
              label="父事件引用"
              value={
                d.parent_refs?.length
                  ? d.parent_refs
                  : e.parent_refs ||
                    e.parent_event_refs ||
                    e.frozen?.parent_event_id ||
                    e.parent_event_id || { reason: "无父事件引用" }
              }
            />
            <Json label="确认时点证据" value={d.confirmation_evidence} />
            <Json label="完整事件与规则版本" value={e} />
          </Panel>
          <Panel title="后续标签与费用证据">
            <p className="rv-note">
              每个经济事件有多期限 / 入场 /
              成本标签。标签行数不等于事件数；BEARISH 不取反。
            </p>
            <Table
              rows={d.outcomes}
              columns={[
                ["entry_basis", "入场"],
                ["horizon", "会话"],
                ["cost_tier", "单边bp"],
                ["split", "集合"],
                [
                  "status",
                  "标签状态",
                  (r) => <Status value={r.status} context="label" />,
                ],
                ["entry_market_date", "入场日"],
                ["exit_market_date", "终点日"],
                ["gross_return", "毛收益", (r) => ratio(r.gross_return)],
                ["net_return", "净收益", (r) => ratio(r.net_return)],
                [
                  "reason_code",
                  "缺失原因",
                  (r) => r.reason_code || r.excluded_reason || "—",
                ],
                [
                  "costs",
                  "费用明细",
                  (r) => <Json label="数值与单位" value={r} />,
                ],
              ]}
            />
          </Panel>
          <p className="rv-footnote">源报告：{d.report_hash}</p>
        </>
      )}
    </Shell>
  );
}
