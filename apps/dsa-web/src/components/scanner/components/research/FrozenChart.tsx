import { useId, useState } from "react";
import type { Data } from "../../api/client";
import { params, useResearch, wallTime } from "../../api/research";
import { Feedback, Json, Panel, Table, reasonText, statusText } from "./ui";

export default function FrozenChart({
  runId,
  eventId,
  hash,
  confirmed,
}: {
  runId: string;
  eventId: string;
  hash: string;
  confirmed: boolean;
}) {
  const [asOf, setAsOf] = useState("TRIGGER"),
    [window, setWindow] = useState(60),
    titleId = useId();
  const state = useResearch(
      `/runs/${runId}/events/${eventId}/chart?${params({ as_of: asOf, window })}`,
    ),
    d = state.data;
  const bars: Data[] = d?.bars || [],
    thresholds: Data[] = d?.thresholds || [];
  const available = d?.availability === "AVAILABLE" && d.report_hash === hash;
  const values = bars
    .flatMap((b) => [Number(b.high), Number(b.low)])
    .concat(thresholds.map((t) => Number(t.converted)));
  const low = Math.min(...values),
    high = Math.max(...values),
    pad = Math.max((high - low) * 0.1, 0.01);
  const y = (p: number) =>
    250 - ((p - low + pad) / (high - low + 2 * pad)) * 215;
  const x = (i: number) => 80 + (i * 900) / Math.max(1, bars.length - 1),
    w = Math.min(12, 600 / Math.max(1, bars.length));
  const level =
    thresholds.find((t) => t.field === "level") ||
    thresholds.find((t) => t.field === "entry_anchor");
  return (
    <Panel title="冻结行情与事件图">
      <p className="rv-note">
        只使用所选业务时点已知的行情修订与公司行动；图表截止该时点，不提前展示确认或失效。没有逐时点依据时明确不可用。
      </p>
      <div className="rv-filters">
        <label>
          行情观察时点
          <select value={asOf} onChange={(e) => setAsOf(e.target.value)}>
            <option value="TRIGGER">触发时点</option>
            <option value="CONFIRMATION" disabled={!confirmed}>
              实际确认时点
            </option>
            <option value="LAST_EVENT_STATE">最后一次事件状态时点</option>
          </select>
        </label>
        <label>
          行情窗口（最多120会话）
          <select
            value={window}
            onChange={(e) => setWindow(Number(e.target.value))}
          >
            {[20, 60, 120].map((v) => (
              <option key={v} value={v}>
                {v}会话
              </option>
            ))}
          </select>
        </label>
      </div>
      <Feedback state={state} />
      {d?.availability === "UNAVAILABLE" && (
        <p className="rv-notice">{reasonText(d.reason)}</p>
      )}
      {d && d.report_hash !== hash && (
        <p role="alert">行情来源与当前报告不一致，停止展示。</p>
      )}
      {available && (
        <>
          <p>
            截至 {wallTime(d.evaluation_clock)} · UTC {d.evaluation_clock} ·
            USD，按该时点可见行动调整。空心为收高，实心为收低。图表为指标价格单位；标签的价格收益或总回报另按报告口径计算。
          </p>
          <div className="rv-chart-scroll">
            <svg
              className="rv-candles"
              viewBox="0 0 1080 310"
              role="img"
              aria-labelledby={titleId}
            >
              <title id={titleId}>
                {d.instrument_id} 截至{d.evaluation_clock}
                的逐时点冻结OHLC，纵轴美元，横轴交易日
              </title>
              {[0, 0.25, 0.5, 0.75, 1].map((p) => {
                const v = low + (high - low) * p;
                return (
                  <g key={p}>
                    <line
                      x1="75"
                      x2="990"
                      y1={y(v)}
                      y2={y(v)}
                      className="rv-chart-grid"
                    />
                    <text x="68" y={y(v) + 4} textAnchor="end">
                      {v.toFixed(2)}
                    </text>
                  </g>
                );
              })}
              {bars.map((b, i) => (
                <g key={b.market_date}>
                  <title>
                    {b.market_date} O {b.open} H {b.high} L {b.low} C {b.close}{" "}
                    · revision {b.revision}
                  </title>
                  <line
                    x1={x(i)}
                    x2={x(i)}
                    y1={y(Number(b.high))}
                    y2={y(Number(b.low))}
                    className="rv-candle-wick"
                  />
                  <rect
                    x={x(i) - w / 2}
                    width={w}
                    y={Math.min(y(Number(b.open)), y(Number(b.close)))}
                    height={Math.max(
                      1,
                      Math.abs(y(Number(b.close)) - y(Number(b.open))),
                    )}
                    className={
                      Number(b.close) >= Number(b.open)
                        ? "rv-candle-up"
                        : "rv-candle-down"
                    }
                  />
                </g>
              ))}
              {level && (
                <line
                  x1={x(
                    Math.max(
                      0,
                      bars.findIndex((b) => b.market_date >= level.known_from),
                    ),
                  )}
                  x2={x(bars.length - 1)}
                  y1={y(Number(level.converted))}
                  y2={y(Number(level.converted))}
                  className="rv-threshold"
                />
              )}
              {d.markers.map((m: Data, i: number) => {
                const n = bars.findIndex(
                  (b) => b.market_date === m.market_date,
                );
                return n < 0 ? null : (
                  <g key={i}>
                    <title>
                      {m.market_date} ·{" "}
                      {m.kind === "TRIGGER" ? "触发" : statusText(m.kind)}
                    </title>
                    <circle
                      cx={x(n)}
                      cy={y(Number(bars[n].high)) - 10 - (i % 2) * 9}
                      r={4}
                      className="rv-event-marker"
                    />
                  </g>
                );
              })}
              <text x="80" y="283">
                {bars[0]?.market_date}
              </text>
              <text x="980" y="283" textAnchor="end">
                {bars.at(-1)?.market_date}
              </text>
              <text x="520" y="306" textAnchor="middle">
                交易日 · OHLC单位 USD
              </text>
            </svg>
          </div>
          <p className="rv-note">
            虚线表示原冻结结构阈值在所选时点的可比单位；圆点表示当时已经发生的事件状态。来源报告：
            {hash}
          </p>
          <Table
            rows={d.markers}
            columns={[
              ["market_date", "事件交易日"],
              [
                "kind",
                "事件标记",
                (r) => (r.kind === "TRIGGER" ? "触发" : statusText(r.kind)),
              ],
              ["evaluated_at", "已知时间（UTC）"],
            ]}
          />
          <Table
            rows={thresholds}
            columns={[
              ["field", "阈值字段"],
              ["original", "原始冻结值"],
              ["converted", "所选时点换算值"],
              ["known_from", "首次冻结交易日"],
              ["unit", "单位"],
            ]}
          />
          <details className="rv-details">
            <summary>OHLC数字替代表与修订证据</summary>
            <Table
              rows={bars}
              columns={[
                ["market_date", "交易日"],
                ["open", "开盘 USD"],
                ["high", "最高 USD"],
                ["low", "最低 USD"],
                ["close", "收盘 USD"],
                ["revision", "当时可见修订"],
                ["available_at", "可知时点 UTC"],
              ]}
            />
          </details>
          <Json label="当时可见公司行动依据" value={d.action_refs} />
        </>
      )}
    </Panel>
  );
}
