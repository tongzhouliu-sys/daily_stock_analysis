import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";
import { useData, Panel, State, SignalList } from "../components/ui";
export default function Signals() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const view = params.get("view") || "RESEARCH";
  const grade = params.get("grade") || "";
  const cursor = params.get("cursor");
  const path =
    "/api/signals?view=" +
    view +
    (grade ? "&grade=" + grade : "") +
    (cursor ? "&cursor=" + encodeURIComponent(cursor) : "");
  const { data, error, loading } = useData(path);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">SIGNALS & WATCHLIST</p>
          <h1>信号与观察</h1>
          <p>保留触发依据、反方证据与失效条件。高分不等于可以创建计划。</p>
        </div>
      </div>
      <div className="toolbar">
        <div className="segmented">
          <button
            className={view === "RESEARCH" ? "selected" : ""}
            onClick={() => setParams({ view: "RESEARCH" })}
          >
            研究观察
          </button>
          <button
            className={view === "PRODUCTION" ? "selected" : ""}
            onClick={() => setParams({ view: "PRODUCTION" })}
          >
            正式候选
          </button>
        </div>
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="搜索当前页标的"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索当前页标的"
          />
        </label>
        <select
          aria-label="筛选等级"
          value={grade}
          onChange={(e) => setParams({ view, grade: e.target.value })}
        >
          <option value="">全部等级</option>
          <option value="HIGH">重点 HIGH</option>
          <option value="TRACK">跟踪 TRACK</option>
          <option value="WATCH">观察 WATCH</option>
        </select>
      </div>
      {view === "RESEARCH" && (
        <div className="notice">
          本页包含尚未获准主动推荐的规则结果，仅供研究；不会据此自动交易。
        </div>
      )}
      <Panel>
        <State loading={loading} error={error} />
        {data && (
          <>
            <SignalList
              items={data.items.filter((c: { instrument: { ticker: string } }) =>
                c.instrument.ticker.toLowerCase().includes(query.toLowerCase()),
              )}
            />
            <div className="pagination">
              <span>
                共{data.total}条，当前页{data.items.length}条
              </span>
              {cursor && (
                <button
                  className="secondary"
                  onClick={() => setParams({ view, grade })}
                >
                  返回首页
                </button>
              )}
              {data.next_cursor && (
                <button
                  className="secondary"
                  onClick={() =>
                    setParams({ view, grade, cursor: data.next_cursor })
                  }
                >
                  下一页
                </button>
              )}
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
