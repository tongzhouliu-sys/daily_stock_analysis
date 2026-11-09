import { useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  mutateResearch,
  useResearch,
  wallTime,
  type ResearchRunDTO,
} from "../../api/research";
import {
  reasonText,
  Back,
  Feedback,
  Json,
  Panel,
  Shell,
  Status,
} from "../../components/research/ui";
export default function Run() {
  const { runId } = useParams(),
    [version, setVersion] = useState(0),
    state = useResearch<ResearchRunDTO>(`/runs/${runId}?view=${version}`, true),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [cancelReason, setCancelReason] = useState("停止本次计算"),
    cancelKey = useRef(crypto.randomUUID()),
    key = useRef(crypto.randomUUID());
  const r = state.data;
  return (
    <Shell
      title={r?.name || "运行详情"}
      subtitle="关闭或刷新页面后，研究计算继续在独立进程中执行。"
      actions={<Back to="/scanner/rule-validation" />}
    >
      <Feedback state={state} />
      {r && (
        <>
          <div className="rv-metrics">
            <div>
              <span>作业状态</span>
              <Status value={r.status} />
            </div>
            <div>
              <span>实际阶段</span>
              <strong>
                {(
                  {
                    QUEUED: "等待研究计算槽",
                    REPLAY: "规则回放",
                    UNCONDITIONAL_BENCHMARK: "无条件基准",
                    INDEXING: "构建报告索引",
                    COMPLETED: "已结束",
                    REPORT_PUBLICATION: "发布完整报告",
                    VALIDATING: "核验冻结输入",
                    COMPARISON: "比较冻结报告",
                    FAILED: "失败",
                    INTERRUPTED: "进程中断",
                    CANCEL_REQUESTED: "等待计算在完整性边界退出",
                    CANCELLED: "本次计算已取消",
                  } as Record<string, string>
                )[r.phase] || r.phase}
              </strong>
            </div>
            <div>
              <span>真实阶段处理数</span>
              <strong>
                {r.processed ?? "—"} / {r.total ?? "未知"}
              </strong>
            </div>
            <div>
              <span>报告状态</span>
              <Status value={r.report_state} />
            </div>
          </div>
          <Panel title="执行健康">
            <p>
              尝试次数：{r.attempt} · 最后心跳：{wallTime(r.heartbeat_at)}
            </p>
            {r.status === "RUNNING" &&
              r.heartbeat_at &&
              Date.now() - Date.parse(r.heartbeat_at) > 20000 && (
                <div className="rv-notice">
                  心跳已陈旧，请检查研究 worker。此提示不会把运行状态改为失败。
                </div>
              )}
            <p className="rv-note">
              阶段之间的总量不同。没有可靠总数时不显示整体百分比。
            </p>
            {r.report_state === "UNSUPPORTED_VERSION" && (
              <div className="rv-notice">
                该历史报告完整性已核验，但当前版本暂不支持其结构。保留登记身份，不提供未经审查的附件或图表。
              </div>
            )}
            <Status
              value={
                r.data_kind === "SYNTHETIC"
                  ? "合成工程样本"
                  : "真实数据 · 证据另核验"
              }
            />
            {r.error && (
              <div role="alert" className="rv-notice rv-danger">
                {reasonText(r.error.code)}
                <Json label="错误详情（供维护）" value={r.error} />
              </div>
            )}
            {error && <p role="alert">{error}</p>}
            {r.status === "CANCEL_REQUESTED" && (
              <p role="status">
                已请求停止真实计算。超过30秒仍未退出时，由持有计算进程的 worker
                终止子进程；结束前仍占用计算槽。
              </p>
            )}
            {r.cancellation && (
              <Json label="取消请求与退出方式" value={r.cancellation} />
            )}
            {!r.external && ["QUEUED", "RUNNING"].includes(r.status) && (
              <div className="rv-filters">
                <label>
                  取消原因
                  <input
                    value={cancelReason}
                    maxLength={500}
                    onChange={(e) => setCancelReason(e.target.value)}
                  />
                </label>
                <button
                  disabled={busy || !cancelReason.trim()}
                  onClick={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      await mutateResearch(
                        `/runs/${runId}/cancel`,
                        { expected_attempt: r.attempt, reason: cancelReason },
                        cancelKey.current,
                      );
                      setVersion((v) => v + 1);
                    } catch (e) {
                      setError((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  取消本次计算
                </button>
              </div>
            )}
            <div className="rv-actions">
              {r.report_state === "READY" && (
                <Link
                  className="button primary"
                  to={`/scanner/rule-validation/runs/${runId}/report`}
                >
                  查看可视化报告
                </Link>
              )}
              {["FAILED", "CANCELLED"].includes(r.status) && !r.external && (
                <button
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    try {
                      await mutateResearch(
                        `/runs/${runId}/retry`,
                        { expected_attempt: r.attempt },
                        key.current,
                      );
                      key.current = crypto.randomUUID();
                      setVersion((v) => v + 1);
                    } catch (e) {
                      setError((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  相同输入重试
                </button>
              )}
              <Link
                className="button"
                to={`/scanner/rule-validation/new?copy=${runId}`}
              >
                复制为新实验
              </Link>
            </div>
            <Json
              label="执行身份与尝试记录"
              value={{
                run_id: r.id,
                engine_job_id: r.engine_job_id,
                logical_key: r.logical_key,
                attempts: r.attempts,
              }}
            />
          </Panel>
        </>
      )}
    </Shell>
  );
}
