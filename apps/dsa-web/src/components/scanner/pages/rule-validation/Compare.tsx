import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { mutateResearch, params, useResearch } from "../../api/research";
import type { Data } from "../../api/client";
import {
  reasonText,
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
export default function Compare() {
  const [search, setSearch] = useSearchParams(),
    list = useResearch("/runs?limit=200"),
    [left, setLeft] = useState(search.get("left") || ""),
    [right, setRight] = useState(search.get("right") || ""),
    [axis, setAxis] = useState("rules"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [kind, setKind] = useState("added"),
    [cursor, setCursor] = useState(0),
    key = useRef(crypto.randomUUID());
  const cid = search.get("comparison_id"),
    result = useResearch(cid ? `/comparisons/${cid}` : null, true),
    c = result.data,
    changes = useResearch(
      c?.status === "SUCCEEDED"
        ? `/comparisons/${cid}/changes?${params({ kind, cursor })}`
        : null,
    ),
    ls = useResearch(
      c?.status === "SUCCEEDED" ? `/runs/${c.left_run_id}/statistics` : null,
    ),
    rs = useResearch(
      c?.status === "SUCCEEDED" ? `/runs/${c.right_run_id}/statistics` : null,
    );
  const options = (list.data?.items || []).filter(
    (r: Data) => r.report_state === "READY",
  );
  const change = () => {
    key.current = crypto.randomUUID();
  };
  return (
    <Shell
      title="实验对比"
      subtitle="固定其他口径，只改变一个声明轴。保留全部结果，禁止自动选优。"
      actions={<Back to="/scanner/rule-validation" />}
    >
      <Panel title="选择两个已就绪报告">
        <div className="rv-form-grid">
          <label>
            左侧实验
            <select
              value={left}
              onChange={(e) => {
                setLeft(e.target.value);
                change();
              }}
            >
              <option value="">请选择</option>
              {options.map((r: Data) => (
                <option key={r.id} value={r.id}>
                  {r.name} · {r.data_kind}
                </option>
              ))}
            </select>
          </label>
          <label>
            右侧实验
            <select
              value={right}
              onChange={(e) => {
                setRight(e.target.value);
                change();
              }}
            >
              <option value="">请选择</option>
              {options.map((r: Data) => (
                <option key={r.id} value={r.id}>
                  {r.name} · {r.data_kind}
                </option>
              ))}
            </select>
          </label>
          <label>
            唯一变化轴
            <select
              value={axis}
              onChange={(e) => {
                setAxis(e.target.value);
                change();
              }}
            >
              {[
                ["rules", "规则版本"],
                ["universe", "证券池"],
                ["data", "冻结数据"],
                ["price_basis", "价格口径"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="rv-note">
          服务器强制核验时钟、标签截止、切分、数据类型、统计 / 代码 /
          依赖版本、费用、基准和样本阈值；不满足则拒绝数值比较。
        </p>
        <button
          className="primary"
          disabled={!left || !right || left === right || busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const c = await mutateResearch(
                "/comparisons",
                { left_run_id: left, right_run_id: right, axis },
                key.current,
              );
              setSearch({ comparison_id: c.id });
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          校验可比性并对比
        </button>
        {error && <p role="alert">{error}</p>}
      </Panel>
      <Feedback state={result} />
      {c && (
        <Panel title="对比结果">
          <Status value={c.status} />
          {c.error && (
            <div role="alert" className="rv-notice rv-danger">
              {reasonText(c.error.code)}
              <span className="rv-note">{c.error.code}</span>
            </div>
          )}
          {c.status === "SUCCEEDED" && (
            <>
              <p>比较轴：{c.axis} · 仅为历史比较，不构成新的未见封存验证。</p>
              <div className="rv-columns">
                <div>
                  <Link
                    className="rv-link"
                    to={`/scanner/rule-validation/runs/${c.left_run_id}/report`}
                  >
                    左侧报告
                  </Link>
                  <Quantiles
                    rows={(ls.data?.items || []).map((x: Data) => ({
                      ...x,
                      series: "左侧",
                    }))}
                  />
                </div>
                <div>
                  <Link
                    className="rv-link"
                    to={`/scanner/rule-validation/runs/${c.right_run_id}/report`}
                  >
                    右侧报告
                  </Link>
                  <Quantiles
                    rows={(rs.data?.items || []).map((x: Data) => ({
                      ...x,
                      series: "右侧",
                    }))}
                  />
                </div>
              </div>
              <Json
                value={{
                  axis: c.axis,
                  left_hash: c.left_hash,
                  right_hash: c.right_hash,
                  compatibility: c.compatibility,
                  configuration_diff: c.configuration_diff,
                  counts: c.counts,
                  summary: c.summary,
                }}
                label="配置与比较来源"
              />
              <label>
                事件差异
                <select
                  value={kind}
                  onChange={(e) => {
                    setKind(e.target.value);
                    setCursor(0);
                  }}
                >
                  <option value="added">新增</option>
                  <option value="removed">移除</option>
                  <option value="changed">变化</option>
                </select>
              </label>
              <Feedback state={changes} />
              {changes.data && (
                <>
                  <Table
                    rows={changes.data.items}
                    columns={
                      kind === "changed"
                        ? [
                            ["key", "事件匹配键"],
                            ["before", "变化前"],
                            ["after", "变化后"],
                          ]
                        : [
                            ["rule_id", "规则"],
                            ["instrument_id", "证券"],
                            ["trigger_market_date", "交易日"],
                            ["lifecycle", "生命周期"],
                            ["event_id", "事件身份"],
                          ]
                    }
                  />
                  <Pager
                    cursor={cursor}
                    next={changes.data.next_cursor}
                    onPage={setCursor}
                  />
                </>
              )}
            </>
          )}
        </Panel>
      )}
    </Shell>
  );
}
