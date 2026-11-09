import { scannerApiPath } from "../api/client";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Download, Play, RefreshCw, ShieldCheck } from "lucide-react";
import { api, type Data } from "../api/client";
import {
  useData,
  Panel,
  State,
  Badge,
  Empty,
  Message,
  dateTime,
  label,
} from "../components/ui";
export default function Settings() {
  const [refresh, setRefresh] = useState(0),
    [message, setMessage] = useState<string | null>(null),
    [busy, setBusy] = useState(false);
  const status = useData("/api/data-status", refresh);
  const dashboard = useData("/api/dashboard", refresh);
  const [key, setKey] = useState("risk");
  const conf = useData("/api/configs/" + key, refresh);
  const [edited, setEdited] = useState(""),
    [reason, setReason] = useState("");
  const [scope, setScope] = useState("PAPER"),
    [name, setName] = useState("我的纸面账户"),
    [cash, setCash] = useState(""),
    [daybase, setDaybase] = useState(""),
    [monthbase, setMonthbase] = useState(""),
    [positions, setPositions] = useState("[]"),
    [complete, setComplete] = useState(false);
  const [exportId, setExportId] = useState(""),
    [deleteConfirm, setDeleteConfirm] = useState("");
  useEffect(() => {
    if (conf.data) setEdited(JSON.stringify(conf.data.value, null, 2));
  }, [conf.data]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      setRefresh((x) => x + 1);
    } catch (ex) {
      setMessage((ex as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">SETTINGS & DATA HEALTH</p>
          <h1>设置与数据状态</h1>
          <p>能力、权限、覆盖与新鲜度分开核验。未配置的功能保持关闭。</p>
        </div>
        <button className="secondary" onClick={() => setRefresh((x) => x + 1)}>
          <RefreshCw size={16} />
          刷新状态
        </button>
      </div>
      <Message text={message} />
      <State loading={status.loading} error={status.error} />
      <Panel title="独立数据来源">
        <div className="source-grid">
          {status.data?.sources?.map((s: Data) => (
            <div className="source-card" key={s.source}>
              <div className="panel-heading">
                <h3>{s.source}</h3>
                <Badge value={s.status} />
              </div>
              {s.latest_value != null && (
                <p className="source-value">
                  VIX {s.latest_value} · 观察日期{" "}
                  {s.as_of || s.latest_market_date}
                </p>
              )}
              <p>
                {s.symbol || s.series_id || "财报日历"} ·{" "}
                {s.latest_market_date || "暂无已核验日期"}
              </p>
              <div className="source-health">
                {Object.entries(s.health).map(([k, v]) => (
                  <span key={k}>
                    {(
                      {
                        connection: "连接",
                        permission: "权限",
                        freshness: "新鲜度",
                        quality: "质量",
                      } as Data
                    )[k] || k}{" "}
                    <Badge value={v} />
                  </span>
                ))}
              </div>
              {s.capabilities?.map((c: Data) => (
                <small key={c.capability_id}>
                  {c.capability_id} · {label(c.support)} /{" "}
                  {label(c.entitlement)} / {c.enabled ? "已启用" : "未启用"}
                </small>
              ))}
              <small>
                数据截至 {dateTime(s.data_as_of)}；采集{" "}
                {dateTime(s.observed_at)}
              </small>
              {s.reason_code && <p className="footnote">{s.reason_code}</p>}
              {s.rth_scope_verified === false && (
                <p className="footnote">
                  供应商EOD口径；RTH范围与量覆盖尚未独立核验，正式计划关闭。
                </p>
              )}
            </div>
          ))}
        </div>
      </Panel>
      <div className="two-columns">
        <Panel title="数据能力">
          <div className="event-row">
            <span>调度进程</span>
            <Badge value={status.data?.worker || "UNKNOWN"} />
          </div>
          <div className="event-row">
            <span>行情源</span>
            <Badge value={status.data?.provider || "UNKNOWN"} />
          </div>
          {status.data?.capabilities?.length ? (
            status.data.capabilities.map((c: Data) => (
              <div className="job-row" key={c.capability_id}>
                <span>
                  {c.capability_id}
                  <small>核验 {dateTime(c.verified_at)}</small>
                </span>
                <div>
                  <Badge value={c.support} />
                  <Badge value={c.entitlement} />
                  <Badge value={c.enabled ? "ENABLED" : "DISABLED"} />
                </div>
              </div>
            ))
          ) : (
            <Empty title="还没有已核验的行情能力">
              通过本地导入工具登记证券与日线，再配置来源、标的池和事件覆盖。真实数据权限仍需单独核验。
            </Empty>
          )}
          <p className="footnote">
            运行说明见项目README。行情凭据仅保存在服务端环境中。
          </p>
        </Panel>
        <Panel title="建立独立账户">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                await api("/api/accounts", "POST", {
                  scope,
                  origin: scope === "PAPER" ? "USER_PLAN" : "MANUAL",
                  currency: "USD",
                  initial_cash: cash,
                  start_market_date: dashboard.data!.action_market_date,
                  name,
                  initial_positions:
                    scope === "MANUAL" ? JSON.parse(positions) : [],
                  day_open_equity: scope === "MANUAL" ? daybase : null,
                  month_open_equity: scope === "MANUAL" ? monthbase : null,
                  funds_complete: complete,
                });
                setMessage(
                  "账户已建立。请查看并确认当前账户快照完整；纸面账户不是真实资产。",
                );
                setCash("");
              });
            }}
          >
            <div className="form-grid">
              <label>
                账户类型
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                >
                  <option value="PAPER">用户纸面账户</option>
                  <option value="MANUAL">实盘自报账户</option>
                </select>
              </label>
              <label>
                名称
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                />
              </label>
            </div>
            <label>
              {scope === "PAPER"
                ? "明确的模拟初始资金（USD）"
                : "实际已结算现金（USD）"}
              <input
                inputMode="decimal"
                required
                value={cash}
                onChange={(e) => setCash(e.target.value)}
              />
            </label>
            {scope === "MANUAL" && (
              <>
                <div className="form-grid">
                  <label>
                    日初实际净值
                    <input
                      required
                      value={daybase}
                      onChange={(e) => setDaybase(e.target.value)}
                    />
                  </label>
                  <label>
                    月初实际净值
                    <input
                      required
                      value={monthbase}
                      onChange={(e) => setMonthbase(e.target.value)}
                    />
                  </label>
                </div>
                <label>
                  当前持仓JSON（确为空仓填[]）
                  <textarea
                    value={positions}
                    onChange={(e) => setPositions(e.target.value)}
                    placeholder="instrument_id、qty、avg_price、mark、stop_reference"
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={complete}
                    onChange={(e) => setComplete(e.target.checked)}
                  />
                  确认已提供全部持仓及日/月资金流，未遗漏账户事实
                </label>
              </>
            )}
            <button
              className="primary"
              disabled={busy || !cash || !dashboard.data}
            >
              <ShieldCheck size={16} />
              建立账户
            </button>
          </form>
        </Panel>
      </div>
      <Panel title="参数与来源配置">
        <div className="toolbar">
          <select
            aria-label="配置类别"
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              setReason("");
            }}
          >
            {[
              ["risk", "风险参数"],
              ["preferences", "时区、晨间时窗和提醒"],
              ["universe", "已登记标的池"],
              ["provider", "行情来源"],
              ["events", "事件覆盖"],
              ["experiment", "研究协议"],
              ["rules", "规则成熟度"],
            ].map(([k, v]) => (
              <option value={k} key={k}>
                {v}
              </option>
            ))}
          </select>
          <span className="muted">
            当前版本 {conf.data?.version || "—"}；调高风险参数延至下一会话生效
          </span>
        </div>
        <State loading={conf.loading} error={conf.error} />
        <details open={key === "risk"}>
          <summary>高级配置编辑（必须说明变更理由）</summary>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              act(async () => {
                const r = await api("/api/configs/" + key, "PUT", {
                  expected_version: conf.data!.version,
                  change_reason: reason,
                  value: JSON.parse(edited),
                });
                setMessage(
                  "配置已保存：" + r.version + "；生效方式 " + r.activation,
                );
              });
            }}
          >
            <label>
              配置内容
              <textarea
                className="code-input"
                rows={12}
                value={edited}
                onChange={(e) => setEdited(e.target.value)}
                required
              />
            </label>
            <label>
              变更理由
              <input
                required
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="说明修改依据和影响"
              />
            </label>
            <button className="primary" disabled={!reason || busy}>
              校验并保存新版本
            </button>
          </form>
        </details>
        <p className="footnote">
          来源只接受运行目录imports内的文件名；不提供自动订阅、账户同步或订单执行开关。
        </p>
      </Panel>
      <Panel title="作业与恢复">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>任务</th>
                <th>状态</th>
                <th>创建时点</th>
                <th>原因</th>
                <th>恢复</th>
              </tr>
            </thead>
            <tbody>
              {status.data?.jobs.map((j: Data) => (
                <tr key={j.id}>
                  <td>{j.job_name}</td>
                  <td>
                    <Badge value={j.status} />
                  </td>
                  <td>{dateTime(j.created_at)}</td>
                  <td>{j.error_json?.code || "—"}</td>
                  <td>
                    {["DAILY_SCAN", "VERIFY", "STATUS"].includes(
                      j.job_name,
                    ) && (
                      <button
                        className="ghost"
                        disabled={busy}
                        onClick={() =>
                          act(async () => {
                            const old = await api("/api/jobs/" + j.id);
                            await api("/api/scans/retry", "POST", {
                              logical_key: old.logical_key,
                              reason: "用户检查后明确要求重试",
                            });
                            setMessage("已排队重试；没有补发历史即时提醒。");
                          })
                        }
                      >
                        明确重试
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            act(async () => {
              const ex = await api("/api/configs/experiment");
              await api("/api/research/runs", "POST", {
                experiment_config_version: ex.version,
                mode: "REPLAY",
                notifications: false,
              });
              setMessage(
                "旧版纸面研究已排队。规则验证请使用独立的规则验证模块。",
              );
            })
          }
        >
          <Play size={16} />
          运行旧版纸面研究（REPLAY）
        </button>
        <p className="muted">
          以上为旧版纸面路径。独立冻结数据回放、统计和事件证据请进入{" "}
          <Link className="button" to="/scanner/rule-validation">
            规则验证
          </Link>
          。
        </p>
      </Panel>
      <Panel title="周度数据体检">
        {status.data?.data_health ? (
          <>
            <div className="health-grid">
              {status.data.data_health.data_health?.map((c: Data) => (
                <div className="event-row" key={c.code}>
                  <span>
                    {c.code}
                    <small>{c.reason}</small>
                  </span>
                  <Badge value={c.status} />
                </div>
              ))}
            </div>
            <p>
              冻结回放：
              <Badge value={status.data.data_health.replay_probe?.status} />
            </p>
          </>
        ) : (
          <Empty title="尚无周度体检报告">
            周六周报会复用已保存运行证据，检查范围和未检查项都会披露。不会自动联网补数。
          </Empty>
        )}
      </Panel>
      <Panel title="个人数据与隐私">
        <div className="button-row">
          <button
            className="secondary"
            disabled={busy}
            onClick={() =>
              act(async () => {
                const r = await api("/api/privacy/export", "POST", {
                  scope: "PERSONAL_DATA",
                  format: "zip",
                });
                setExportId(r.job_id);
                setMessage("个人数据导出已排队。完成后使用下方按钮下载。");
              })
            }
          >
            <Download size={16} />
            申请个人数据导出
          </button>
          {exportId && (
            <button
              className="secondary"
              onClick={() => window.location.assign(scannerApiPath("/api/exports/" + exportId))}
            >
              下载已完成的导出
            </button>
          )}
        </div>
        <details>
          <summary>删除情绪与原文（先完成导出）</summary>
          <p>会清除决策原文、情绪及关联缓存。历史备份随轮转到期。</p>
          <label>
            已完成的导出任务ID
            <input
              value={exportId}
              onChange={(e) => setExportId(e.target.value)}
            />
          </label>
          <label>
            输入 DELETE_REQUESTED 明确确认
            <input
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
            />
          </label>
          <button
            className="danger-button"
            disabled={busy || !exportId || deleteConfirm !== "DELETE_REQUESTED"}
            onClick={() =>
              act(async () => {
                await api("/api/privacy/delete", "POST", {
                  scope: "EMOTION_AND_NOTES",
                  export_id: exportId,
                  confirmation: "DELETE_REQUESTED",
                });
                setMessage("隐私删除请求已排队，请查看作业结果。");
              })
            }
          >
            提交删除请求
          </button>
        </details>
      </Panel>
    </>
  );
}
