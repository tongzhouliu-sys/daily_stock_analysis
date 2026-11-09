import { useState } from "react";
import { useResearch, wallTime } from "../../api/research";
import type { Data } from "../../api/client";
import {
  Back,
  Empty,
  Feedback,
  Json,
  Pager,
  Panel,
  Shell,
  Status,
  Table,
} from "../../components/research/ui";

export default function Probes() {
  const [cursor, setCursor] = useState(0),
    [selected, setSelected] = useState<Data | null>(null),
    state = useResearch(`/probes?cursor=${cursor}`),
    d = state.data;
  return (
    <Shell
      title="周度冻结核验历史"
      subtitle="只读查看既有WEEKLY_REPORT，不启动核验或完整历史回放。"
      actions={<Back to="/scanner/rule-validation" />}
    >
      <div className="rv-notice">
        PASS只表示指定冻结扫描的复算一致，不代表完整历史研究通过、规则有效或已获批准。最新报告损坏时保留失败，不换用更早成功样本。
      </div>
      <Feedback state={state} />
      {d && d.items.length === 0 && (
        <Empty>尚无既有周度报告。此页面不会生成新报告或补算历史。</Empty>
      )}
      {d && d.items.length > 0 && (
        <>
          <div className="rv-metrics">
            {[
              ["PASS", "快照一致"],
              ["FAIL", "核验失败"],
              ["SKIPPED", "未执行核验"],
              ["VERSION_UNAVAILABLE", "版本不可用"],
            ].map(([s, label]) => (
              <div key={s}>
                <span>本页 · {label}</span>
                <strong>
                  {d.items.filter((r: Data) => r.status === s).length}
                </strong>
              </div>
            ))}
          </div>
          {cursor === 0 && d.items[0].integrity === "INVALID" && (
            <p role="alert" className="rv-notice rv-danger">
              最新周度报告完整性异常，未回退到更早报告。
            </p>
          )}
          <Panel title="既有报告与实际核验结果">
            <Table
              rows={d.items}
              columns={[
                [
                  "created_at",
                  "报告/记录时间",
                  (r) => wallTime(r.generated_at || r.created_at),
                ],
                [
                  "status",
                  "核验结果",
                  (r) =>
                    r.status ? <Status value={r.status} /> : "周度报告尚未完成",
                ],
                ["reason", "原因"],
                [
                  "source_scan_ids",
                  "源扫描",
                  (r) => (r.source_scan_ids || []).join(" / ") || "—",
                ],
                ["network_calls", "实际外联次数"],
                ["account_writes", "实际越界/账户写入次数"],
                [
                  "details",
                  "证据",
                  (r) => (
                    <button onClick={() => setSelected(r)}>查看核验记录</button>
                  ),
                ],
              ]}
            />
            <Pager
              cursor={cursor}
              next={d.next_cursor}
              limit={30}
              onPage={setCursor}
            />
          </Panel>
          {selected && (
            <Panel title="所选核验的冻结证据">
              <p>源扫描：{selected.source_scan_ids.join(" / ") || "—"}</p>
              <p className="rv-footnote">
                预期业务hash：{selected.expected_hash || "未知/未核验"}
              </p>
              <p className="rv-footnote">
                实际业务hash：{selected.actual_hash || "未知/未核验"}
              </p>
              <p className="rv-note">
                没有计数证据时显示“—”，不当作0次外联或0次写入。
              </p>
              <Json label="完整只读核验记录" value={selected} />
            </Panel>
          )}
        </>
      )}
    </Shell>
  );
}
