import { useState } from "react";
import { api, type Data } from "../api/client";
import {
  useData,
  Panel,
  State,
  Empty,
  Badge,
  Message,
  Metric,
  number,
  dateTime,
  label,
} from "../components/ui";
export default function Journal() {
  const dash = useData("/api/dashboard");
  const [account, setAccount] = useState(""),
    [dataset, setDataset] = useState("SIGNAL_OUTCOME"),
    [window, setWindow] = useState("ALL"),
    [refresh, setRefresh] = useState(0),
    [message, setMessage] = useState<string | null>(null),
    [input, setInput] = useState(""),
    [checked, setChecked] = useState<Data | null>(null);
  const [decisionCursor, setDecisionCursor] = useState<string | null>(null);
  const [ledgerCursor, setLedgerCursor] = useState<string | null>(null);
  const needsAccount = ["PAPER_STRATEGY", "MANUAL_RESULT"].includes(dataset);
  const report = useData(
    needsAccount && !account
      ? null
      : "/api/reviews?dataset=" +
          dataset +
          "&section=SUMMARY&rolling_window=" +
          (dataset === "USER_DECISIONS" ? "ALL" : window) +
          (needsAccount ? "&account_id=" + account : ""),
    refresh,
  );
  const ledger = useData(
    account
      ? "/api/reviews?section=LEDGER&account_id=" +
          account +
          (ledgerCursor ? "&cursor=" + encodeURIComponent(ledgerCursor) : "")
      : null,
    refresh,
  );
  const decisions = useData(
    "/api/reviews?section=DECISIONS" +
      (decisionCursor ? "&cursor=" + encodeURIComponent(decisionCursor) : ""),
    refresh,
  );
  const [reverseId, setReverseId] = useState(""),
    [reverseReason, setReverseReason] = useState("");
  const [busy, setBusy] = useState(false);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      setDecisionCursor(null);
      setLedgerCursor(null);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">JOURNAL & REVIEW</p>
          <h1>日志与复盘</h1>
          <p>记录判断，也记录等待。完成样本、未到期与未解决结果分别查看。</p>
        </div>
      </div>
      <Message text={message} />
      <div className="toolbar">
        <select
          aria-label="结果类型"
          value={dataset}
          onChange={(e) => setDataset(e.target.value)}
        >
          <option value="SIGNAL_OUTCOME">信号后续分布</option>
          <option value="PAPER_STRATEGY">用户纸面账本</option>
          <option value="MANUAL_RESULT">自报实盘结果</option>
          <option value="USER_DECISIONS">晨间决策过程</option>
        </select>
        <select
          aria-label="账户"
          value={account}
          onChange={(e) => {
            setAccount(e.target.value);
            setChecked(null);
            setLedgerCursor(null);
          }}
        >
          <option value="">选择账户</option>
          {dash.data?.accounts.map((a: Data) => (
            <option key={a.id} value={a.id}>
              {a.name} · {label(a.scope)}
            </option>
          ))}
        </select>
        <select
          aria-label="滚动窗口"
          value={window}
          disabled={dataset === "USER_DECISIONS"}
          onChange={(e) => setWindow(e.target.value)}
        >
          <option value="ALL">当前区间全部</option>
          <option value="50">最近50条完成样本</option>
          <option value="100">最近100条完成样本</option>
        </select>
      </div>
      <Panel title="结果与样本口径">
        <State loading={report.loading} error={report.error} />
        {report.data?.process_metrics ? (
          <div>
            <div className="metric-grid">
              <Metric
                name="晨间首次决策"
                value={report.data.process_metrics.morning_count}
                note="时窗包含开始、不包含结束"
              />
              <Metric
                name="全部首次决策"
                value={report.data.process_metrics.total_first_decisions}
                note="每张卡/目标日期仅取历史首次"
              />
              <Metric
                name="晨间占比"
                value={
                  report.data.process_metrics.morning_decision_share == null
                    ? "—"
                    : number(
                        Number(
                          report.data.process_metrics.morning_decision_share,
                        ) * 100,
                      ) + "%"
                }
                note="仅作描述，不推断阅读或收益因果"
              />
            </div>
          </div>
        ) : report.data?.groups?.length ? (
          report.data.groups.map((g: Data, i: number) => (
            <div className="report-group" key={i}>
              <p className="muted">
                {g.group.rule_id || label(g.group.origin)} ·{" "}
                {g.group.horizon || "完整平仓"} · {g.group.data_mode}
              </p>
              <div className="metric-grid">
                <Metric
                  name="实际完成样本"
                  value={g.sample_size}
                  note={`目标窗口 ${g.target_window}`}
                />
                <Metric
                  name="未到期 / 未解决"
                  value={`${g.pending} / ${g.unresolved}`}
                  note="不填0收益，不从报告隐藏"
                />
                <Metric
                  name="盈利样本率"
                  value={
                    g.win_rate == null
                      ? "—"
                      : number(Number(g.win_rate) * 100) + "%"
                  }
                  note="持平进入分母；非预测概率"
                />
                <Metric
                  name="累计净损益"
                  value={number(g.cumulative_closed_pnl)}
                  note="入选平仓累计，非完整账户回撤"
                />
              </div>
            </div>
          ))
        ) : (
          !report.loading && (
            <Empty
              title={
                needsAccount && !account
                  ? "请先选择对应账户"
                  : "尚无可计算的完成样本"
              }
            >
              信号统计需要先运行离线研究。未到期、费用待核或资料不足的结果不会作为零收益参与统计。
            </Empty>
          )
        )}
      </Panel>
      <div className="two-columns">
        <Panel title="已提交判断">
          <State loading={decisions.loading} error={decisions.error} />
          {decisions.data?.items?.length ? (
            decisions.data.items.map((d: Data) => (
              <div className="journal-entry" key={d.id}>
                <Badge value={d.action} />
                <p>{d.reason}</p>
                <small>
                  {dateTime(d.server_time)} · {d.action_market_date}
                </small>
              </div>
            ))
          ) : (
            <Empty title="还没有提交判断">
              浏览不会自动创建决策。可在信号详情选择等待、忽略或加入观察。
            </Empty>
          )}
          <div className="button-row">
            {decisionCursor && (
              <button className="ghost" onClick={() => setDecisionCursor(null)}>
                返回首批判断
              </button>
            )}
            {decisions.data?.next_cursor && (
              <button
                className="secondary"
                onClick={() => setDecisionCursor(decisions.data!.next_cursor)}
              >
                更早的判断
              </button>
            )}
          </div>
        </Panel>
        <Panel title="成交与资金事实">
          <State loading={ledger.loading} error={ledger.error} />
          {ledger.data?.items?.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>类型 / 时间</th>
                    <th>数量</th>
                    <th>现金变动</th>
                    <th>记录</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.data.items.map((r: Data) => (
                    <tr key={r.id}>
                      <td>
                        {r.entry_type}
                        <small>{dateTime(r.occurred_at)}</small>
                      </td>
                      <td>{r.qty ?? "—"}</td>
                      <td>{number(r.cash_amount)}</td>
                      <td>
                        <button
                          className="ghost"
                          onClick={() => setReverseId(r.id)}
                        >
                          选择冲正
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title={account ? "暂无已录入事实" : "尚未选择账户"}>
              自报成交与纸面模拟分开；实际成交不因风险超计划而被隐瞒。
            </Empty>
          )}
          <div className="button-row">
            {ledgerCursor && (
              <button className="ghost" onClick={() => setLedgerCursor(null)}>
                返回首批事实
              </button>
            )}
            {ledger.data?.next_cursor && (
              <button
                className="secondary"
                onClick={() => setLedgerCursor(ledger.data!.next_cursor)}
              >
                更早的事实
              </button>
            )}
          </div>
        </Panel>
      </div>
      <Panel title="人工账本导入">
        <p className="muted">
          先校验、后明确提交。费用、时区与稳定证券ID必须填写；不会因导入记录而执行交易。
        </p>
        <details>
          <summary>查看一行JSON格式</summary>
          <pre>{`[{"external_key":"你的凭据唯一编号","entry_type":"BUY","instrument_id":"已登记证券ID","occurred_at":"带时区的实际成交时间","qty":"10","price":"100.00","fee":"1.00","currency":"USD"}]`}</pre>
        </details>
        <label>
          事实记录JSON
          <textarea
            rows={7}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setChecked(null);
            }}
            placeholder="粘贴实际事实；示例不是已发生成交"
          />
        </label>
        <div className="button-row">
          <button
            className="secondary"
            disabled={!account || !input || busy}
            onClick={() =>
              act(async () => {
                const r = await api("/api/ledger/import", "POST", {
                  account_id: account,
                  import_id: crypto.randomUUID(),
                  dry_run: true,
                  rows: JSON.parse(input),
                });
                setChecked(r);
                setMessage(
                  `校验：有效${r.valid}行，错误${r.invalid}行，重复${r.duplicates}行。尚未提交。`,
                );
              })
            }
          >
            仅校验
          </button>
          <button
            className="primary"
            disabled={!checked || checked.invalid > 0 || busy}
            onClick={() =>
              act(async () => {
                await api("/api/ledger/import", "POST", {
                  account_id: account,
                  import_id: crypto.randomUUID(),
                  dry_run: false,
                  rows: JSON.parse(input),
                });
                setMessage("事实已提交，账户需按新版本重新核对。");
                setInput("");
                setChecked(null);
                setRefresh((x) => x + 1);
              })
            }
          >
            确认提交有效批次
          </button>
        </div>
        {checked?.errors?.length > 0 && (
          <pre>{JSON.stringify(checked?.errors, null, 2)}</pre>
        )}
        <details>
          <summary>纠正已录入事实</summary>
          <label>
            被冲正的记录ID
            <input
              value={reverseId}
              onChange={(e) => setReverseId(e.target.value)}
            />
          </label>
          <label>
            纠错理由
            <input
              value={reverseReason}
              onChange={(e) => setReverseReason(e.target.value)}
            />
          </label>
          <button
            className="secondary"
            disabled={!reverseId || !reverseReason || busy}
            onClick={() =>
              act(async () => {
                await api("/api/ledger/" + reverseId + "/reverse", "POST", {
                  reason: reverseReason,
                });
                setMessage("已追加冲正，原始记录保留。需按新快照重新核对。");
                setRefresh((x) => x + 1);
              })
            }
          >
            追加冲正
          </button>
        </details>
      </Panel>
    </>
  );
}
