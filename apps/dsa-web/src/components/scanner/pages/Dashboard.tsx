import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, CheckCheck, Pause, ShieldAlert } from "lucide-react";
import { api, type Data } from "../api/client";
import {
  useData,
  Panel,
  Metric,
  Badge,
  State,
  Empty,
  SessionStrip,
  SignalList,
  Message,
  dateTime,
  label,
} from "../components/ui";
export default function Dashboard() {
  const [refresh, setRefresh] = useState(0),
    [message, setMessage] = useState<string | null>(null);
  const { data: d, error, loading } = useData("/api/dashboard", refresh);
  const signals = useData("/api/signals?view=RESEARCH&limit=5", refresh);
  const context = useData("/api/market-context");
  const [mission, setMission] = useState(""),
    [events, setEvents] = useState(false),
    [risk, setRisk] = useState(false);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">DAILY WORKSPACE</p>
          <h1>今日工作台</h1>
          <p>先确认数据和风险，再审阅值得研究的信号。</p>
        </div>
        <button className="secondary" onClick={() => setRefresh((x) => x + 1)}>
          刷新已存结果
        </button>
      </div>
      <State loading={loading} error={error} />
      {d && (
        <>
          {d.fixture_mode && (
            <div className="notice">
              当前是虚构验证环境：价格、账户和信号仅用于测试，不代表真实行情或已接通服务。
            </div>
          )}
          <SessionStrip context={context.data || d.session_context} />
          <Message text={message} />
          <div className="metric-grid">
            <Metric
              name="市场环境"
              value={<Badge value={d.environment.mode} />}
              note="以有效SPY / VIX数据为依据"
            />
            <Metric
              name="本轮数据覆盖"
              value={
                d.coverage.expected
                  ? `${d.coverage.ok} / ${d.coverage.expected}`
                  : "尚未配置"
              }
              note={
                d.coverage.failed
                  ? `${d.coverage.failed}个标的待处理`
                  : "缺失数据不从分母剔除"
              }
            />
            <Metric
              name="研究候选"
              value={signals.data?.total ?? "—"}
              note="12条核心规则；未批准仅供观察"
            />
            <Metric
              name="数据截至"
              value={
                <span className="metric-time">
                  {d.data_as_of ? dateTime(d.data_as_of) : "暂无有效快照"}
                </span>
              }
              note="时间附展示时区；EOD不代表实时报价"
            />
          </div>
          {d.state === "UNCONFIGURED" && (
            <Panel>
              <div className="onboarding">
                <div className="onboarding-icon">
                  <ShieldAlert size={25} />
                </div>
                <div>
                  <h2>先接好数据，再讨论机会。</h2>
                  <p>
                    还没有可用的扫描快照。配置标的池与数据来源后，系统会按实际交易日历扫描；不会使用占位数据生成机会。
                  </p>
                  <Link to="/scanner/settings" className="text-link">
                    配置数据与标的池 <ArrowUpRight size={16} />
                  </Link>
                </div>
              </div>
            </Panel>
          )}
          <div className="two-columns">
            <Panel
              title="隔夜风险与账户核对"
              action={<span className="section-note">始终可见</span>}
            >
              {d.overnight_checklist?.length ? (
                d.overnight_checklist.map((a: Data, i: number) => (
                  <div className="risk-item" key={a.account_id || i}>
                    <ShieldAlert size={18} />
                    <div>
                      <b>
                        {a.account_id
                          ? `${label(a.scope)} · ${label(a.origin)}`
                          : "尚未建立已核对账户"}
                      </b>
                      <p>
                        {label(a.positions_status)}
                        。参考止损不代表券商已有有效订单。
                      </p>
                      {a.items?.map((x: Data, j: number) => (
                        <div key={j} className="minor-row">
                          <Badge value={x.status} />
                          <span>{x.reason || x.code}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))
              ) : (
                <Empty title="账户完整性待确认">
                  添加账户后再核对现金、持仓与资金流。未核对不代表零风险。
                </Empty>
              )}
            </Panel>
            <Panel title="下一会话与事件">
              <div className="event-row">
                <span>目标交易会话</span>
                <b>{d.action_market_date}</b>
              </div>
              <div className="event-row">
                <span>下一常规开盘</span>
                <b>{dateTime(context.data?.next_rth_open?.utc)}</b>
              </div>
              {context.data?.next_events?.length ? (
                context.data.next_events.map((e: Data) => (
                  <div className="event-row" key={e.event_id}>
                    <span>{e.event_type}</span>
                    <b>{dateTime(e.scheduled_at)}</b>
                  </div>
                ))
              ) : (
                <div className="notice">
                  当前未查到已知未来事件；覆盖未核验时不能据此判断没有风险。
                </div>
              )}
              <p className="footnote">
                会话开放、行情覆盖和报价新鲜度分别核验。
              </p>
            </Panel>
          </div>
          <Panel
            title="晨间研究摘要"
            action={
              <Link to="/scanner/signals" className="text-link">
                全部候选 <ArrowUpRight size={16} />
              </Link>
            }
          >
            <State loading={signals.loading} error={signals.error} />
            {signals.data && <SignalList items={signals.data.items} />}
            <div className="panel-footnote">
              {d.digest?.message ||
                "摘要以完成常规会话为基准；不承诺完整盘后覆盖。"}
            </div>
          </Panel>
          <div className="two-columns">
            <Panel title="每日准备">
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    await api("/api/preflight", "PUT", {
                      action_market_date: d.action_market_date,
                      events_seen: events,
                      event_hash: d.events_hash,
                      risk_seen: risk,
                      risk_version: d.risk_version,
                      mission,
                    });
                    setMessage(
                      "准备已保存；正式计划仍需STOP、账户与预算校验。",
                    );
                  } catch (ex) {
                    setMessage((ex as Error).message);
                  }
                }}
              >
                <label className="check">
                  <input
                    type="checkbox"
                    checked={events}
                    onChange={(e) => setEvents(e.target.checked)}
                  />
                  已查看当前事件及覆盖限制
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={risk}
                    onChange={(e) => setRisk(e.target.checked)}
                  />
                  已查看风险参数及未知信息
                </label>
                <label>
                  今天的审阅原则
                  <input
                    value={mission}
                    required
                    maxLength={500}
                    onChange={(e) => setMission(e.target.value)}
                    placeholder="写下你准备遵守的计划"
                  />
                </label>
                <button className="primary">
                  <CheckCheck size={16} />
                  保存每日准备
                </button>
              </form>
            </Panel>
            <Panel
              title="运行记录"
              action={
                <Link to="/scanner/settings" className="text-link">
                  数据状态
                </Link>
              }
            >
              {d.jobs.length ? (
                d.jobs.slice(0, 5).map((j: Data) => (
                  <div className="job-row" key={j.id}>
                    <span>
                      {j.job_name}
                      <small>{dateTime(j.created_at)}</small>
                    </span>
                    <Badge value={j.status} />
                  </div>
                ))
              ) : (
                <Empty title="等待首次运行">
                  调度进程独立运行，刷新页面不会启动扫描。
                </Empty>
              )}
              <button
                className="ghost"
                onClick={async () => {
                  try {
                    await api("/api/user-pause", "POST", {
                      paused: true,
                      reason: "用户主动暂停方向提醒",
                    });
                    setMessage("方向提醒已暂停，风险信息仍然可见。");
                  } catch (ex) {
                    setMessage((ex as Error).message);
                  }
                }}
              >
                <Pause size={16} />
                暂停方向提醒
              </button>
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
