import { scannerApiPath } from "../api/client";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, FileText, ShieldAlert } from "lucide-react";
import { api, type Data } from "../api/client";
import {
  useData,
  Panel,
  State,
  Badge,
  Message,
  dateTime,
  label,
  number,
  Empty,
  scoreLabel,
  scoreNote,
} from "../components/ui";
export default function Detail() {
  const { id } = useParams();
  const [refresh, setRefresh] = useState(0),
    [message, setMessage] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  const card = useData("/api/signals/" + id);
  const dashboard = useData("/api/dashboard", refresh);
  const c = card.data;
  const bars = useData(
    c ? "/api/instruments/" + c.instrument_id + "/bars?limit=260" : null,
  );
  const [aid, setAid] = useState("");
  const acc = useData(aid ? "/api/accounts/" + aid + "/risk" : null, refresh);
  const [entryInput, setEntryInput] = useState<{id?: string; value: string} | null>(null);
  const [stopInput, setStopInput] = useState<{id?: string; value: string} | null>(null);
  const entry = entryInput && entryInput.id === id ? entryInput.value : String(c?.suggested_entry || c?.entry_anchor || "");
  const stop = stopInput && stopInput.id === id ? stopInput.value : String(c?.suggested_stop || "");
  const setEntry = (value: string) => setEntryInput({id, value});
  const setStop = (value: string) => setStopInput({id, value});
  const [reason, setReason] = useState(""),
    [expectation, setExpectation] = useState(""),
    [invalidation, setInvalidation] = useState(""),
    [emotion, setEmotion] = useState("Calm");
  const [session, setSession] = useState<Data | null>(null),
    [draft, setDraft] = useState<Data | null>(null),
    [preview, setPreview] = useState<Data | null>(null);
  const day = dashboard.data?.action_market_date;
  const prep = useData(
    day ? "/api/preflight?action_market_date=" + day : null,
    refresh,
  );
  const intent = () => ({ account_id: aid, entry, stop });
  const base = () => ({
    ...intent(),
    card_id: id,
    card_revision: c?.revision,
    action_market_date: day,
  });
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
    } catch (ex) {
      setMessage((ex as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!c)
    return (
      <>
        <State loading={card.loading} error={card.error} />
        <Link to="/scanner/signals">返回信号列表</Link>
      </>
    );
  const chart = bars.data?.items || [];
  const prices = chart.map((b: Data) => Number(b.close));
  const low = Math.min(...prices),
    high = Math.max(...prices);
  const points = prices
    .map(
      (p: number, i: number) =>
        `${20 + (i / Math.max(1, prices.length - 1)) * 760},${170 - ((p - low) / (high - low || 1)) * 140}`,
    )
    .join(" ");
  return (
    <>
      <Link to="/scanner/signals" className="back-link">
        <ArrowLeft size={16} />
        返回信号与观察
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">SIGNAL EVIDENCE</p>
          <h1>
            {c.instrument.ticker}{" "}
            <span className="muted">{c.instrument.name}</span>
          </h1>
          <p>
            {c.primary_rule} · <Badge value={c.rule_stage} /> ·{" "}
            {dateTime(c.data_as_of)}
          </p>
        </div>
        <div className="detail-status">
          <Link className="button" to={`/chat?stock=${encodeURIComponent(c.instrument.ticker)}&name=${encodeURIComponent(c.instrument.name || "")}&scannerSignal=${encodeURIComponent(c.id)}&scannerRevision=${c.revision}`}>带入证据继续 AI 研究</Link>
          <Badge value={c.lifecycle} />
          <Badge value={c.availability} />
        </div>
      </div>
      <Message text={message} />
      <div className="notice">
        <ShieldAlert size={18} />
        <span>
          质量分 {scoreLabel(c)} · {scoreNote(c)}。
          {c.reason_codes?.join(" · ") || "创建计划仍需重检账户、STOP与预算。"}
        </span>
      </div>
      <div className="two-columns detail-columns">
        <div>
          <Panel
            title="价格与冻结依据"
            action={<span className="section-note">调整后日线 · EOD参考</span>}
          >
            <State loading={bars.loading} error={bars.error} />
            {prices.length ? (
              <svg
                className="price-chart"
                viewBox="0 0 800 200"
                role="img"
                aria-label="调整后收盘价折线图"
              >
                <line x1="20" y1="170" x2="780" y2="170" stroke="#34343a" />
                <polyline
                  points={points}
                  fill="none"
                  stroke="#8d97eb"
                  strokeWidth="2"
                />
                <text x="20" y="195" fill="#aeb4c0" fontSize="13">
                  {chart[0]?.market_date}
                </text>
                <text x="640" y="195" fill="#aeb4c0" fontSize="13">
                  {chart.at(-1)?.market_date}
                </text>
              </svg>
            ) : (
              <Empty title="图表数据不足">缺失时不补齐价格曲线。</Empty>
            )}
            <div className="detail-numbers">
              <div>
                <small>冻结入场锚点</small>
                <b>{number(c.entry_anchor)}</b>
              </div>
              <div>
                <small>距离锚点</small>
                <b>
                  {c.distance_to_entry_pct == null
                    ? "—"
                    : number(c.distance_to_entry_pct) + "%"}
                </b>
              </div>
              <div>
                <small>ATR距离</small>
                <b>{number(c.distance_to_entry_atr)}</b>
              </div>
            </div>
          </Panel>
          <Panel title="触发与评分明细">
            {!c.score_trace.length && <p>{scoreNote(c)}</p>}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>检查项</th>
                    <th>结果</th>
                    <th>分数</th>
                  </tr>
                </thead>
                <tbody>
                  {c.score_trace.map((x: Data) => (
                    <tr key={x.check}>
                      <td>{x.check}</td>
                      <td>{x.passed ? "满足" : "未满足"}</td>
                      <td>{x.points}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details>
              <summary>查看原始触发值与冻结位置</summary>
              <pre>
                {JSON.stringify(
                  { trigger: c.trigger_values, frozen: c.frozen_levels },
                  null,
                  2,
                )}
              </pre>
            </details>
          </Panel>
          <Panel title="反方证据与限制">
            <ul className="plain-list">
              {c.bear_cases.map((x: string) => (
                <li key={x}>{x}</li>
              ))}
              <li>
                有效至 {dateTime(c.valid_until)}；价格失效条件以原冻结依据为准。
              </li>
              <li>
                当前统计：{label(c.base_rate.status)}，样本{" "}
                {c.base_rate.sample_size}；不显示未经验证的胜率。
              </li>
            </ul>
          </Panel>
        </div>
        <div>
          <Panel title="记录你的判断">
            <form onSubmit={(e) => e.preventDefault()}>
              <label>
                理由
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="这条信号值得等待或参与的依据是什么？"
                  required
                />
              </label>
              <label>
                预期
                <input
                  value={expectation}
                  onChange={(e) => setExpectation(e.target.value)}
                  placeholder="写下可核对的预期"
                  required
                />
              </label>
              <label>
                证伪条件
                <input
                  value={invalidation}
                  onChange={(e) => setInvalidation(e.target.value)}
                  placeholder="什么情况会使判断失效？"
                  required
                />
              </label>
              <label>
                当前情绪
                <select
                  value={emotion}
                  onChange={(e) => setEmotion(e.target.value)}
                >
                  <option value="Calm">平静</option>
                  <option value="Fear">担忧</option>
                  <option value="Excitement">兴奋</option>
                  <option value="Boredom">无聊</option>
                </select>
              </label>
              <div className="button-row">
                {[
                  ["WAIT", "等待"],
                  ["IGNORE", "忽略"],
                  ["WATCH", "加入观察"],
                ].map(([action, text]) => (
                  <button
                    key={action}
                    className="secondary"
                    disabled={busy || !reason || !expectation || !invalidation}
                    onClick={() =>
                      act(async () => {
                        await api("/api/decisions", "POST", {
                          card_id: id,
                          card_revision: c.revision,
                          action_market_date: day,
                          action,
                          reason,
                          expectation,
                          invalidation,
                          emotion,
                        });
                        setMessage("判断已记录；没有产生订单或预算占用。");
                      })
                    }
                  >
                    {text}
                  </button>
                ))}
              </div>
            </form>
          </Panel>
          <Panel title="参考计划与风险预算">
            <label>
              账户
              <select
                value={aid}
                onChange={(e) => {
                  setAid(e.target.value);
                  setSession(null);
                  setDraft(null);
                }}
              >
                <option value="">请明确选择账户</option>
                {dashboard.data?.accounts.map((a: Data) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {label(a.scope)}
                  </option>
                ))}
              </select>
            </label>
            {!dashboard.data?.accounts.length && (
              <Link className="text-link" to="/scanner/settings">
                先建立纸面或自报账户
              </Link>
            )}
            <div className="form-grid">
              <label>
                参考入场价
                <input
                  inputMode="decimal"
                  value={entry}
                  onChange={(e) => {
                    setEntry(e.target.value);
                    setSession(null);
                    setDraft(null);
                  }}
                />
              </label>
              <label>
                参考止损价
                <input
                  inputMode="decimal"
                  value={stop}
                  onChange={(e) => {
                    setStop(e.target.value);
                    setSession(null);
                    setDraft(null);
                  }}
                />
              </label>
            </div>
            <p className="footnote">
              价格为原始报价单位。改动计划逻辑会要求新STOP；数量由服务器计算。
            </p>
            {acc.data && (
              <div className="account-summary">
                <Badge value={acc.data.status} />
                <span>净值 {number(acc.data.snapshot.equity)} USD</span>
                <button
                  className="ghost"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await api("/api/accounts/" + aid + "/confirm", "POST", {
                        snapshot_id: acc.data!.snapshot_id,
                        ledger_head: acc.data!.ledger_head,
                        complete: true,
                        reason: "已逐项核对现金、持仓、基准和资金流",
                      });
                      setRefresh((x) => x + 1);
                      setMessage(
                        "当前账户版本已核对。后续账本变更会要求重新确认。",
                      );
                    })
                  }
                >
                  确认此账户快照完整
                </button>
              </div>
            )}
            <div className="button-row">
              <button
                className="secondary"
                disabled={busy || !aid || !entry || !stop}
                onClick={() =>
                  act(async () => {
                    const r = await api("/api/risk/preview", "POST", base());
                    setPreview(r);
                  })
                }
              >
                预览风险
              </button>
              <button
                className="secondary"
                disabled={busy || !aid || !entry || !stop}
                onClick={() =>
                  act(async () => {
                    const r = await api("/api/plans", "POST", {
                      ...base(),
                      intent: "DRAFT",
                    });
                    setDraft(r);
                    setMessage("草稿已保存；尚未保留预算或执行订单。");
                  })
                }
              >
                <FileText size={15} />
                保存草稿
              </button>
            </div>
            {preview && (
              <div className="notice">
                <span>
                  {preview.status === "CALCULATED"
                    ? `预览：最多${preview.qty}股，计划风险${preview.planned_risk} USD；未占用。`
                    : `暂不可计算正式数量：${preview.all_reasons?.join(" · ")}`}
                </span>
              </div>
            )}
            {draft && (
              <div className="minor-row">
                <Badge value={draft.status} />
                <span>计划版本 {draft.revision}</span>
                <button
                  className="ghost"
                  onClick={() =>
                    act(async () => {
                      const r = await api(
                        "/api/plans/" + draft.id + "/export",
                        "POST",
                        {
                          expected_revision: draft.revision,
                          format: "markdown",
                          basis:
                            draft.status === "RESERVED"
                              ? "EOD_REFERENCE"
                              : "HISTORICAL",
                        },
                      );
                      window.location.assign(scannerApiPath(r.download_url));
                    })
                  }
                >
                  <Download size={15} />
                  导出参考参数
                </button>
              </div>
            )}
          </Panel>
          <Panel title="STOP · 决策前检查">
            <p className="muted">
              只有明确打开此流程才开始30分钟冷静期。等待、忽略与查看风险不受限。
            </p>
            {!session ? (
              <button
                className="secondary"
                disabled={busy || !aid || !entry || !stop}
                onClick={() =>
                  act(async () => {
                    const r = await api("/api/stop-sessions", "POST", {
                      card_id: id,
                      card_revision: c.revision,
                      action_market_date: day,
                      plan_intent: intent(),
                    });
                    setSession(r);
                  })
                }
              >
                开始STOP审阅
              </button>
            ) : (
              <>
                <div className="steps">
                  {["S", "T", "O", "P"].map((s) => (
                    <span
                      className={
                        s === (session.next_step || session.step)
                          ? "active"
                          : ""
                      }
                      key={s}
                    >
                      {s}
                    </span>
                  ))}
                </div>
                <p>可继续正式校验时间：{dateTime(session.cooling_until)}</p>
                {(session.next_step || session.step) !== "READY" ? (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        const step = session.next_step || session.step;
                        const p: Data = {
                          expected_revision: session.revision,
                          step,
                        };
                        if (step === "S") p.acknowledged = true;
                        if (step === "T") p.emotion = emotion;
                        if (step === "O") {
                          p.evidence_hash = c.evidence_hash;
                          p.read_ack = true;
                        }
                        if (step === "P")
                          Object.assign(p, {
                            reason,
                            expectation,
                            invalidation,
                          });
                        const r = await api(
                          "/api/stop-sessions/" + session.id,
                          "PATCH",
                          p,
                        );
                        setSession({ ...session, ...r });
                      })
                    }
                  >
                    确认当前步骤：{session.next_step || session.step}
                  </button>
                ) : (
                  <div className="notice">
                    四步已完成；服务器仍会检查冷静期、最新证据及预算。
                  </div>
                )}
              </>
            )}
            <button
              className="primary full-width"
              disabled={
                busy ||
                !draft ||
                !session ||
                !acc.data ||
                !prep.data ||
                c.availability !== "PLAN_ALLOWED"
              }
              onClick={() =>
                act(async () => {
                  const r = await api(
                    "/api/plans/" + draft!.id + "/reserve",
                    "POST",
                    {
                      expected_revision: draft!.revision,
                      card_revision: c.revision,
                      account_snapshot_id: acc.data!.snapshot_id,
                      risk_profile_version: dashboard.data!.risk_version,
                      preflight_id: prep.data!.id,
                      stop_session_id: session!.id,
                      account_confirmation: "确认所选账户快照与资金流完整",
                    },
                  );
                  setDraft(r);
                  setRefresh((x) => x + 1);
                  setMessage("计划预算已保留。系统没有执行订单。");
                })
              }
            >
              校验并保留计划预算
            </button>
            <p className="footnote">研究态和资料不全时，正式计划保持不可用。</p>
          </Panel>
        </div>
      </div>
    </>
  );
}
