import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { type Data } from "../../api/client";
import { mutateResearch, useResearch } from "../../api/research";
import {
  reasonText,
  Back,
  Empty,
  Feedback,
  Json,
  Panel,
  Shell,
  Status,
} from "../../components/research/ui";
const saved = () => {
  try {
    return JSON.parse(localStorage.getItem("rv-draft") || "null");
  } catch {
    return null;
  }
};
export default function New() {
  const catalog = useResearch("/catalog"),
    [search] = useSearchParams(),
    copy = useResearch(
      search.get("copy") ? `/runs/${search.get("copy")}` : null,
    );
  const [form, setForm] = useState<Data | null>(saved),
    [vid, setVid] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [step, setStep] = useState(1);
  const validation = useResearch(vid ? `/validations/${vid}` : null, true),
    nav = useNavigate();
  const keys = useRef({
    validation: crypto.randomUUID(),
    run: crypto.randomUUID(),
  });
  useEffect(() => {
    if (copy.data?.draft) {
      setForm({
        dataset_id: copy.data.dataset_id,
        draft: {
          ...copy.data.draft,
          name: `${copy.data.name}（副本）`,
          registration_id: `protocol-${crypto.randomUUID()}`,
        },
      });
      setVid(null);
    }
  }, [copy.data]);
  useEffect(() => {
    if (form) localStorage.setItem("rv-draft", JSON.stringify(form));
  }, [form]);
  const change = (key: string, value: unknown) => {
    setForm((f) => (f ? { ...f, draft: { ...f.draft, [key]: value } } : f));
    setVid(null);
    setStep(2);
    keys.current = {
      validation: crypto.randomUUID(),
      run: crypto.randomUUID(),
    };
  };
  const ds = catalog.data?.datasets?.find(
    (d: Data) => d.id === form?.dataset_id,
  );
  const valid =
    validation.data?.result_status === "VALID" &&
    validation.data?.status === "SUCCEEDED";
  async function validate() {
    setBusy(true);
    setError("");
    try {
      const v = await mutateResearch(
        "/validations",
        { dataset_id: form?.dataset_id, draft: form?.draft },
        keys.current.validation,
      );
      setVid(v.id);
      setStep(3);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    setBusy(true);
    setError("");
    try {
      const r = await mutateResearch(
        "/runs",
        {
          validation_id: vid,
          config_digest: validation.data?.config_digest,
          reveal_sealed: false,
        },
        keys.current.run,
      );
      localStorage.removeItem("rv-draft");
      nav(`/scanner/rule-validation/runs/${r.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const field = (label: string, key: string, type = "text") => (
    <label>
      {label}
      <input
        required
        type={type}
        value={form?.draft[key] ?? ""}
        onChange={(e) =>
          change(
            key,
            type === "number" ? Number(e.target.value) : e.target.value,
          )
        }
      />
    </label>
  );
  return (
    <Shell
      title="新建实验"
      subtitle="先固定研究协议，再看冻结样本。全 12 条规则执行，结果筛选不改变运行协议。"
      actions={<Back to="/scanner/rule-validation" />}
    >
      <div className="rv-steps" aria-label="新建步骤">
        {["选择数据", "设置协议", "校验与修复", "确认并运行"].map((s, i) => (
          <button
            key={s}
            aria-current={step === i + 1 ? "step" : undefined}
            disabled={(!form && i > 0) || (i === 3 && !valid)}
            onClick={() => {
              setStep(i + 1);
              document
                .getElementById(`rv-step-${i + 1}`)
                ?.scrollIntoView({ block: "start", behavior: "auto" });
            }}
          >
            {i + 1}. {s}
          </button>
        ))}
      </div>
      <Feedback state={catalog} />
      {copy.data?.copy_unavailable && (
        <div className="rv-notice">{copy.data.copy_unavailable}</div>
      )}
      {error && (
        <div role="alert" className="rv-notice rv-danger">
          {error}
        </div>
      )}
      <div id="rv-step-1">
        <Panel title="冻结数据">
          <label>
            选择已登记数据包
            <select
              value={form?.dataset_id || ""}
              onChange={(e) => {
                const d = catalog.data?.datasets.find(
                  (x: Data) => x.id === e.target.value,
                );
                setForm(
                  d ? { dataset_id: d.id, draft: d.default_draft } : null,
                );
                setVid(null);
                keys.current = {
                  validation: crypto.randomUUID(),
                  run: crypto.randomUUID(),
                };
              }}
            >
              <option value="">请选择冻结数据包</option>
              {catalog.data?.datasets?.map((d: Data) => (
                <option key={d.id} value={d.id}>
                  {d.name} ·{" "}
                  {d.data_kind === "SYNTHETIC" ? "合成工程样本" : "真实数据"}
                </option>
              ))}
            </select>
          </label>
          {ds && (
            <p className="rv-note">
              {ds.coverage.start_date} 至 {ds.coverage.end_date} ·{" "}
              {ds.coverage.instruments} 证券 · RTH证据：
              {ds.evidence.rth ? "已登记" : "缺失"} · PIT历史证据：
              {ds.evidence.pit ? "已登记" : "缺失"}
              。当前版本运行资格以预检为准。
            </p>
          )}
          {catalog.data && !catalog.data.datasets.length && (
            <Empty>
              没有已登记数据。请由维护人员准备冻结数据并执行登记命令；网页不接收本机路径或上传大型数据。
            </Empty>
          )}
        </Panel>
      </div>
      {form && (
        <>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              validate();
            }}
          >
            <div id="rv-step-2">
              <Panel title="研究协议">
                <div className="rv-form-grid">
                  {field("实验名称", "name")}
                  <label>
                    研究假设
                    <textarea
                      required
                      maxLength={2000}
                      value={form.draft.hypothesis}
                      onChange={(e) => change("hypothesis", e.target.value)}
                    />
                  </label>
                  {field("协议登记 ID", "registration_id")}
                  {field("开始交易日", "start_date", "date")}
                  {field("结束交易日", "end_date", "date")}
                  {field("预热会话数（至少260）", "warmup_bars", "number")}
                  <label>
                    收盘后分钟
                    <input
                      type="number"
                      min={0}
                      max={1440}
                      value={form.draft.evaluation_clock.minutes}
                      onChange={(e) =>
                        change("evaluation_clock", {
                          kind: "SESSION_CLOSE_PLUS_MINUTES",
                          minutes: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  {field("标签截止（含UTC时区）", "label_as_of")}
                  <label>
                    历史模式
                    <select
                      value={form.draft.research_mode}
                      onChange={(e) => change("research_mode", e.target.value)}
                    >
                      <option value="POINT_IN_TIME">
                        POINT_IN_TIME · 逐时点
                      </option>
                      <option value="REVISED_HISTORY">
                        REVISED_HISTORY · 修订历史
                      </option>
                    </select>
                  </label>
                  <label>
                    收益口径
                    <select
                      value={form.draft.price_return_kind}
                      onChange={(e) =>
                        change("price_return_kind", e.target.value)
                      }
                    >
                      <option value="PRICE_RETURN">价格收益</option>
                      <option value="TOTAL_RETURN">
                        总回报（需行动因子证据）
                      </option>
                    </select>
                  </label>
                  <label>
                    研究切分
                    <select
                      value={form.draft.split_policy.kind}
                      onChange={(e) =>
                        change(
                          "split_policy",
                          e.target.value === "EXPLORATORY"
                            ? { kind: e.target.value }
                            : { kind: e.target.value, embargo_sessions: 20 },
                        )
                      }
                    >
                      <option value="EXPLORATORY">探索性（少于五年）</option>
                      <option value="CHRONOLOGICAL_60_20_20">
                        60/20/20 · 两段20会话隔离
                      </option>
                    </select>
                  </label>
                  {field("最低完成样本数", "min_completed_samples", "number")}
                  <fieldset>
                    <legend>允许的入场口径</legend>
                    {[
                      ["NEXT_AVAILABLE_OPEN", "下一可用开盘"],
                      ["CONFIRM_CLOSE", "确认收盘"],
                    ].map(([v, l]) => (
                      <label className="rv-check" key={v}>
                        <input
                          type="checkbox"
                          checked={form.draft.entry_bases.includes(v)}
                          onChange={(e) =>
                            change(
                              "entry_bases",
                              e.target.checked
                                ? [...form.draft.entry_bases, v]
                                : form.draft.entry_bases.filter(
                                    (x: string) => x !== v,
                                  ),
                            )
                          }
                        />
                        {l}
                      </label>
                    ))}
                  </fieldset>
                </div>
              </Panel>
            </div>
            <Panel title="成本假设">
              <p className="rv-note">
                单边滑点固定为 5 / 10 /
                20bp，入场与终点各施加一次。费用为双边显式值；0费用也需明确填写。
              </p>
              <div className="rv-form-grid">
                <label>
                  费用单位
                  <select
                    value={form.draft.cost_assumption.fee_unit}
                    onChange={(e) =>
                      change("cost_assumption", {
                        ...form.draft.cost_assumption,
                        fee_unit: e.target.value,
                      })
                    }
                  >
                    <option value="BPS_OF_ENTRY">入场金额基点（bp）</option>
                    <option value="FIXED_USD">固定金额（USD）</option>
                  </select>
                </label>
                {[
                  ["entry_fee", "入场费用"],
                  ["exit_fee", "离场费用"],
                  ...(form.draft.cost_assumption.fee_unit === "FIXED_USD"
                    ? [["reference_notional", "参考名义本金（USD）"]]
                    : []),
                ].map(([k, l]) => (
                  <label key={k}>
                    {l}
                    <input
                      required
                      type="number"
                      step="any"
                      min="0"
                      value={form.draft.cost_assumption[k] || "0"}
                      onChange={(e) =>
                        change("cost_assumption", {
                          ...form.draft.cost_assumption,
                          [k]: e.target.value,
                        })
                      }
                    />
                  </label>
                ))}
              </div>
            </Panel>
            <div className="rv-actions">
              <button
                className="primary"
                disabled={busy || !form.draft.entry_bases.length}
              >
                提交异步预检
              </button>
              <span className="rv-note">
                草稿保存在此浏览器，修改配置后必须重新预检。
              </span>
            </div>
          </form>
          <Feedback state={validation} />
          {validation.data && (
            <div id="rv-step-3">
              <Panel title="预检与确认">
                <Status value={validation.data.status} />
                <p>
                  {valid
                    ? "预检有效，请确认以下全部配置。启动后配置冻结。"
                    : "预检尚未通过，不会开始回放。"}
                </p>
                {validation.data.issues?.map((issue: Data, i: number) => (
                  <p role="alert" key={i}>
                    {issue.field === "draft" ? "配置校验" : issue.field}：
                    {reasonText(issue.code)}
                    <small>（{issue.code}）</small>
                  </p>
                ))}
                <Json
                  label="完整规范 manifest（只读）"
                  value={validation.data.manifest}
                />
                {valid && (
                  <div id="rv-step-4" className="rv-confirmation">
                    <h3>确认摘要并运行</h3>
                    <p>
                      数据：{ds?.name} · {ds?.data_kind}；日期：
                      {form.draft.start_date} 至 {form.draft.end_date}；预热：
                      {form.draft.warmup_bars}会话。
                    </p>
                    <p>
                      时钟：收盘后{form.draft.evaluation_clock.minutes}
                      分钟；截止：{form.draft.label_as_of}；模式：
                      {form.draft.research_mode}；收益：
                      {form.draft.price_return_kind}；最低样本：
                      {form.draft.min_completed_samples}。
                    </p>
                    <p>
                      入场：{form.draft.entry_bases.join(" / ")}；单边滑点：5 /
                      10 / 20bp；入场费用：
                      {form.draft.cost_assumption.entry_fee}；离场费用：
                      {form.draft.cost_assumption.exit_fee}；单位：
                      {form.draft.cost_assumption.fee_unit}
                      。全12规则，封存不揭示。
                    </p>
                    <button className="primary" disabled={busy} onClick={start}>
                      确认协议并启动回放
                    </button>
                  </div>
                )}
              </Panel>
            </div>
          )}
          <Json label="当前草稿（不是实验事实）" value={form.draft} />
        </>
      )}
    </Shell>
  );
}
