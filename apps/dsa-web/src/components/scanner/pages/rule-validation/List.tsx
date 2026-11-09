import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, GitCompareArrows } from "lucide-react";
import {
  params,
  useResearch,
  wallTime,
  type ResearchListDTO,
  type CatalogDTO,
} from "../../api/research";
import {
  Empty,
  Feedback,
  Pager,
  Shell,
  Status,
  Table,
} from "../../components/research/ui";
export default function List() {
  const [status, setStatus] = useState(""),
    [kind, setKind] = useState(""),
    [search, setSearch] = useState(""),
    [startDate, setStartDate] = useState(""),
    [endDate, setEndDate] = useState(""),
    [cursor, setCursor] = useState(0),
    [selected, setSelected] = useState<string[]>([]);
  const list = useResearch<ResearchListDTO>(
    `/runs?${params({ status, data_kind: kind, search, cursor, start_date: startDate, end_date: endDate })}`,
    true,
  );
  const catalog = useResearch<CatalogDTO>("/catalog");
  return (
    <Shell
      title="规则验证"
      subtitle="让规则经得起回看，让每个结论有据可查。"
      actions={
        <>
          <Link className="button" to="/scanner/rule-validation/probes">
            周度冻结核验
          </Link>
          <Link
            className="button"
            to={`/scanner/rule-validation/compare?${params({ left: selected[0], right: selected[1] })}`}
          >
            <GitCompareArrows size={17} />
            对比实验{selected.length ? ` (${selected.length}/2)` : ""}
          </Link>
          <Link className="button primary" to="/scanner/rule-validation/new">
            <Plus size={17} />
            新建实验
          </Link>
        </>
      }
    >
      <div className="rv-notice">
        <strong>研究结果需要人工评审</strong>
        <span>
          合成样本仅验证工程。固定会话后续分布不等同于实际成交或组合收益。
        </span>
      </div>
      <div className="rv-metrics">
        <div>
          <span>已登记实验</span>
          <strong>{list.data?.total ?? "—"}</strong>
        </div>
        <div>
          <span>冻结数据包</span>
          <strong>{catalog.data?.datasets?.length ?? "—"}</strong>
        </div>
        <div>
          <span>计算方式</span>
          <strong>独立离线队列</strong>
        </div>
        <div>
          <span>评审出口</span>
          <strong>人工待评审</strong>
        </div>
      </div>
      <div className="rv-filters">
        <label>
          搜索实验
          <input
            value={search}
            placeholder="实验名称"
            onChange={(e) => {
              setSearch(e.target.value);
              setCursor(0);
            }}
          />
        </label>
        <label>
          作业状态
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setCursor(0);
            }}
          >
            <option value="">全部状态</option>
            {[
              "QUEUED",
              "RUNNING",
              "CANCEL_REQUESTED",
              "CANCELLED",
              "SUCCEEDED",
              "PARTIAL",
              "FAILED",
            ].map((s) => (
              <option key={s} value={s}>
                {
                  {
                    QUEUED: "排队中",
                    RUNNING: "运行中",
                    CANCEL_REQUESTED: "正在取消",
                    CANCELLED: "已取消",
                    SUCCEEDED: "作业完成",
                    PARTIAL: "部分完成",
                    FAILED: "执行失败",
                  }[s]
                }
              </option>
            ))}
          </select>
        </label>
        <label>
          数据属性
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setCursor(0);
            }}
          >
            <option value="">全部数据</option>
            <option value="SYNTHETIC">合成工程样本</option>
            <option value="REAL">真实数据</option>
          </select>
        </label>
      </div>
      <div className="rv-filters">
        <label>
          研究开始不早于
          <input
            type="date"
            value={startDate}
            onChange={(e) => {
              setStartDate(e.target.value);
              setCursor(0);
            }}
          />
        </label>
        <label>
          研究结束不晚于
          <input
            type="date"
            value={endDate}
            onChange={(e) => {
              setEndDate(e.target.value);
              setCursor(0);
            }}
          />
        </label>
      </div>
      <Feedback state={list} />
      {catalog.data && !catalog.data.runtime_ready && (
        <Empty>
          研究服务尚未初始化。请按 Web 使用说明初始化独立目录并启动研究 worker。
        </Empty>
      )}
      {list.data && (
        <>
          <Table
            rows={list.data.items}
            columns={[
              [
                "select",
                "对比",
                (r) => (
                  <input
                    aria-label={`选择 ${r.name} 进行对比`}
                    type="checkbox"
                    checked={selected.includes(r.id)}
                    disabled={
                      r.report_state !== "READY" ||
                      (!selected.includes(r.id) && selected.length >= 2)
                    }
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, r.id]
                          : selected.filter((x) => x !== r.id),
                      )
                    }
                  />
                ),
              ],
              [
                "name",
                "实验",
                (r) => (
                  <Link
                    className="rv-link"
                    to={`/scanner/rule-validation/runs/${r.id}${r.report_state === "READY" ? "/report" : ""}`}
                  >
                    {r.name}
                    <small>
                      {r.external
                        ? "外部历史报告 · 登记域未知"
                        : "本域登记实验"}
                    </small>
                  </Link>
                ),
              ],
              [
                "data_kind",
                "数据属性",
                (r) => (
                  <span className="rv-status">
                    {r.data_kind === "SYNTHETIC"
                      ? "合成工程样本"
                      : r.data_kind === "REAL"
                        ? "真实数据 · 证据另核验"
                        : "数据属性未知"}
                  </span>
                ),
              ],
              [
                "range",
                "交易日期",
                (r) => (
                  <>
                    {r.start_date}
                    <br />
                    {r.end_date}
                  </>
                ),
              ],
              [
                "status",
                "作业 / 报告",
                (r) => (
                  <>
                    <Status value={r.status} />
                    <Status value={r.report_state} />
                  </>
                ),
              ],
              [
                "heartbeat_at",
                "心跳 / 创建",
                (r) => <>{wallTime(r.heartbeat_at || r.created_at)}</>,
              ],
              [
                "action",
                "操作",
                (r) => (
                  <Link
                    className="button"
                    to={`/scanner/rule-validation/new?copy=${r.id}`}
                  >
                    复制配置
                  </Link>
                ),
              ],
            ]}
          />
          {!list.data.items.length && (
            <p className="rv-note">
              尚无实验时可新建；旧 CLI 报告须通过管理命令显式登记。
            </p>
          )}
          <Pager
            cursor={cursor}
            next={list.data.next_cursor}
            onPage={setCursor}
          />
        </>
      )}
    </Shell>
  );
}
