import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Data } from "../../api/client";
import {
  mutateResearch,
  params,
  terminal,
  useResearch,
} from "../../api/research";
import { Feedback, Json, Panel, Pager, Status, Table, reasonText } from "./ui";

export default function Variants({ runId }: { runId: string }) {
  const [version, setVersion] = useState(0),
    [cursor, setCursor] = useState(0),
    [validationCursor, setValidationCursor] = useState(0),
    [choiceId, setChoice] = useState(""),
    [value, setValue] = useState(""),
    [vid, setVid] = useState<string | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const plan = useResearch(
      `/runs/${runId}/variants?${params({ view: version, cursor, validation_cursor: validationCursor })}`,
      true,
    ),
    validation = useResearch(vid ? `/validations/${vid}` : null, true),
    keys = useRef({
      validation: crypto.randomUUID(),
      run: crypto.randomUUID(),
    });
  const d = plan.data,
    choices: Data[] = d?.choices || [],
    choice = choices.find((c) => `${c.rule_id}/${c.parameter}` === choiceId);
  const change = () => {
    setVid(null);
    setError("");
    keys.current = {
      validation: crypto.randomUUID(),
      run: crypto.randomUUID(),
    };
  };
  useEffect(() => {
    if (validation.data && terminal(validation.data)) setVersion((v) => v + 1);
  }, [validation.data]);
  return (
    <Panel title="预登记参数变体">
      <p className="rv-note">
        仅开放原实验看结果之前冻结的有限±20%值，每次只改一个参数；整数参数保持整数。所有变体与失败记录均保留，不自动选优。
      </p>
      <Feedback state={plan} />
      {d && !d.available && <p>{reasonText(d.reason)}</p>}
      {d?.sealed_history_used && (
        <div className="rv-notice">
          该封存区间已有查看历史。变体只作历史比较，不会重新获得未见样本身份；揭示记录不会回滚。
        </div>
      )}
      {d?.available && (
        <>
          <Json label="已冻结的预注册与有限变化范围" value={d.registration} />
          <Json
            label="固定轴预览（数据、日期、成本、时钟、截止、切分及代码均不变）"
            value={d.fixed_axes}
          />
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              try {
                const v = await mutateResearch(
                  `/runs/${runId}/variants/validations`,
                  {
                    expected_report_hash: d.report_hash,
                    rule_id: choice?.rule_id,
                    parameter: choice?.parameter,
                    value: Number(value),
                  },
                  keys.current.validation,
                );
                setVid(v.id);
                setVersion((n) => n + 1);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="rv-form-grid">
              <label>
                预登记参数
                <select
                  required
                  value={choiceId}
                  onChange={(e) => {
                    setChoice(e.target.value);
                    setValue("");
                    change();
                  }}
                >
                  <option value="">请选择参数</option>
                  {choices.map((c) => (
                    <option
                      key={`${c.rule_id}/${c.parameter}`}
                      value={`${c.rule_id}/${c.parameter}`}
                    >
                      {c.rule_id} · {c.parameter} · 基准{c.baseline}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                有限变化值
                <select
                  required
                  value={value}
                  disabled={!choice}
                  onChange={(e) => {
                    setValue(e.target.value);
                    change();
                  }}
                >
                  <option value="">请选择预登记值</option>
                  {choice?.values
                    .filter((v: number) => v !== choice.baseline)
                    .map((v: number) => (
                      <option value={v} key={v}>
                        {v}
                        {choice.integer ? "（整数）" : ""}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            <button disabled={busy || !choice || value === ""}>
              提交变体预检
            </button>
          </form>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      <Feedback state={validation} />
      {validation.data && (
        <>
          <p>
            变体预检：
            <Status value={validation.data.status} />
          </p>
          {validation.data.issues?.map((i: Data, n: number) => (
            <p key={n}>{reasonText(i.code)}</p>
          ))}
          <Json label="变化与继承的完整协议" value={validation.data.manifest} />
        </>
      )}
      {validation.data?.result_status === "VALID" && (
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await mutateResearch(
                "/runs",
                {
                  validation_id: vid,
                  config_digest: validation.data?.config_digest,
                  reveal_sealed: false,
                },
                keys.current.run,
              );
              setVid(null);
              setVersion((n) => n + 1);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          确认固定轴并运行变体
        </button>
      )}
      {d && d.items?.length > 0 && (
        <>
          <h3>基准与全部变体结果</h3>
          <Table
            rows={d.items}
            columns={[
              [
                "name",
                "实验",
                (r) => (
                  <Link to={`/scanner/rule-validation/runs/${r.id}`}>{r.name}</Link>
                ),
              ],
              ["status", "实际状态", (r) => <Status value={r.status} />],
              [
                "variant_change",
                "参数变化",
                (r) =>
                  r.variant_change
                    ? `${r.variant_change.rule_id} / ${r.variant_change.parameter}: ${r.variant_change.baseline} → ${r.variant_change.value}`
                    : "原始基准",
              ],
              [
                "actions",
                "查看与单轴对比",
                (r) => (
                  <>
                    {r.report_state === "READY" && (
                      <Link to={`/scanner/rule-validation/runs/${r.id}/report`}>
                        查看报告
                      </Link>
                    )}{" "}
                    {r.id !== d.root_run_id && r.report_state === "READY" && (
                      <Link
                        to={`/scanner/rule-validation/compare?left=${d.root_run_id}&right=${r.id}`}
                      >
                        规则轴对比
                      </Link>
                    )}
                  </>
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
        </>
      )}
      {d && d.validations?.length > 0 && (
        <>
          <Json
            label="全部变体预检审计（含失败）"
            value={d.validations.map((v: Data) => ({
              id: v.id,
              status: v.status,
              change: v.variant_change,
              created_at: v.created_at,
              issues: v.issues,
            }))}
          />
          <Pager
            cursor={validationCursor}
            next={d.next_validation_cursor}
            limit={30}
            onPage={setValidationCursor}
          />
        </>
      )}
    </Panel>
  );
}
