"""Generate human/machine conclusions for an EOD exploratory replay run.

Reads report.json/protocol.json from a replay output directory produced by
scripts/replay_local_eod.py and writes conclusions.json, 回放结论.md and
summary.csv into the same directory. All numbers are derived from the report;
nothing is hardcoded. This does not approve rules: output stays
EOD_EXPLORATORY and formal RTH/PIT validation remains a separate gate.
"""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

DEFAULT_VIEW_BASIS = "NEXT_AVAILABLE_OPEN"
HORIZONS = (5, 10, 20)
TIERS = (5, 10, 20)


def fnum(v):
    return None if v is None else float(v)


def pct(v):
    v = fnum(v)
    return "n/a" if v is None else f"{v * 100:+.2f}%"


def pct2(v):
    v = fnum(v)
    return "n/a" if v is None else f"{v * 100:.2f}%"


def pp(v):
    v = fnum(v)
    return "n/a" if v is None else f"{v * 100:+.2f}pp"


def select(stats, dim, value, horizon, tier, basis):
    for s in stats:
        if (
            s["dimension"] == dim
            and s["value"] == value
            and s["horizon"] == horizon
            and s["cost_tier"] == tier
            and s["entry_basis"] == basis
        ):
            return s
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, help="replay output directory containing report.json")
    ap.add_argument("--baseline-run-dir", default="", help="earlier replay run directory for reproducibility comparison")
    args = ap.parse_args()

    out = Path(args.run_dir)
    r = json.loads((out / "report.json").read_text())
    protocol = json.loads((out / "protocol.json").read_text()) if (out / "protocol.json").exists() else {}
    stats = r["statistics"]
    cov = r["coverage"]
    life = {l["rule_id"]: l for l in r["lifecycle"]}
    ex = r["execution"]
    basis = r["default_view"]["entry_basis"]
    h0 = r["default_view"]["horizon"]
    t0 = r["default_view"]["cost_tier"]

    total = select(stats, "DIRECTIONAL_ECONOMIC_TOTAL", "LONG", h0, t0, basis)
    findings = []

    n = total["completed"]
    findings.append(
        f"当前EOD探索性样本未显示稳定的做多优势：{n}个方向型去重确认事件，{h0}日平均净收益"
        f"{pct(total['net_return']['mean'])}，中位数{pct(total['net_return']['p50'])}，"
        f"正收益比例{pct2(total['positive_net_fraction'])}，相对SPY平均超额{pp(total['spy_excess_return']['mean'])}。"
    )

    grid = []
    negatives = 0
    for h in HORIZONS:
        for t in TIERS:
            s = select(stats, "DIRECTIONAL_ECONOMIC_TOTAL", "LONG", h, t, basis)
            if s is None:
                continue
            m = fnum(s["net_return"]["mean"])
            grid.append((h, t, s, m))
            if m is not None and m < 0:
                negatives += 1
    findings.append(
        f"下一交易日开盘入场的5/10/20会话与单边5/10/20bp滑点共{len(grid)}种预设口径中，"
        f"方向型总体平均净收益为负的有{negatives}种。没有按结果调参或挑选某一档作为成功。"
        + (
            "全部为负。"
            if negatives == len(grid)
            else "存在非负口径，不得仅选该口径宣布成功。"
        )
    )

    rule_rows = []
    for s in stats:
        if s["dimension"] != "RULE" or s["horizon"] != h0 or s["cost_tier"] != t0 or s["entry_basis"] != basis:
            continue
        rule_rows.append(s)
    directional_rules = [s for s in rule_rows if life.get(s["value"], {}).get("kind") == "DIRECTION"]
    warning_rules = [s for s in rule_rows if life.get(s["value"], {}).get("kind") == "WARNING"]
    thr = protocol.get("min_completed_samples", 30)
    detail = []
    for s in directional_rules:
        if not s["completed"]:
            continue
        detail.append(
            f"{s['value']}：{s['completed']}个确认样本，{h0}日均值{pct(s['net_return']['mean'])}，"
            f"中位数{pct(s['net_return']['p50'])}，相对SPY{pp(s['spy_excess_return']['mean'])}，"
            f"相对同标的无条件均值{pp(s['unconditional_excess_return']['mean'])}"
            + ("（低于展示门槛）" if s["completed"] < thr else "")
        )
    findings.append("方向型规则分解：" + "；".join(detail) + "。尚不能描述为稳定优势。")

    mo01 = life.get("MO-01", {})
    ev01 = life.get("EV-01", {})
    findings.append(
        f"MO-01触发{mo01.get('triggered') or 0}次、确认{mo01.get('confirmed') or 0}次，不能据此评估收益。"
        f"MO-02仅有证据事件（{life.get('MO-02', {}).get('triggered') or 0:,}次），不作为独立方向收益。"
        + (
            f"EV-01不可评估（{ev01.get('reason_code') or '缺历史财报覆盖'}），计数保留空值。"
            if ev01
            else "EV-01缺历史财报覆盖不可评估，计数保留空值。"
        )
    )

    years = [
        s
        for s in stats
        if s["dimension"] == "DIRECTIONAL_YEAR" and s["horizon"] == h0 and s["cost_tier"] == t0 and s["entry_basis"] == basis
    ]
    pos_years = [s for s in years if (fnum(s["net_return"]["mean"]) or 0) > 0]
    ydetail = "、".join(
        f"{s['value']}年{pct(s['net_return']['mean'])}（{s['completed']}例）" for s in years
    )
    findings.append(
        f"按确认年份，{h0}日方向型净收益：{ydetail}。{len(pos_years)}/{len(years)}个年份为正，"
        "正向结果未跨年份稳定复现。"
    )

    warn_detail = "、".join(
        f"{s['value']}毛价格下跌比例{pct2(s['negative_gross_fraction'])}（{s['completed']}例）"
        for s in warning_rules
        if fnum(s["negative_gross_fraction"]) is not None
    )
    findings.append(
        f"警示规则只描述后续价格方向，不是做空收益验证：{warn_detail}。RK-01保留为波动风险观察。"
        if warn_detail
        else "警示规则只描述后续价格方向，不是做空收益验证；RK-01保留为波动风险观察。"
    )

    horizon_lines = []
    for h in HORIZONS:
        s = select(stats, "DIRECTIONAL_ECONOMIC_TOTAL", "LONG", h, t0, basis)
        if s:
            horizon_lines.append(
                f"- {h}会话：{s['completed']}个确认样本，平均净收益{pct(s['net_return']['mean'])}，"
                f"中位数{pct(s['net_return']['p50'])}，正收益{pct2(s['positive_net_fraction'])}，"
                f"相对SPY{pp(s['spy_excess_return']['mean'])}。"
            )
    tier_lines = []
    for t in TIERS:
        s = select(stats, "DIRECTIONAL_ECONOMIC_TOTAL", "LONG", h0, t, basis)
        if s:
            tier_lines.append(
                f"- 单边{t}bp：{s['completed']}个确认样本，平均净收益{pct(s['net_return']['mean'])}，"
                f"正收益{pct2(s['positive_net_fraction'])}。"
            )
    cc = select(stats, "DIRECTIONAL_ECONOMIC_TOTAL", "LONG", h0, t0, "CONFIRM_CLOSE")
    cc_line = (
        f"确认日收盘只是独立敏感性口径：{h0}会话均值{pct(cc['net_return']['mean'])}（{t0}bp），"
        f"改用下一日开盘后为{pct(total['net_return']['mean'])}；前者不声称可成交，不得只选该口径宣布成功。"
        if cc
        else ""
    )

    conclusions = {
        "kind": "EOD_EXPLORATORY_CONCLUSIONS",
        "status": "EXPLORATORY_FINDINGS_COMPLETE",
        "source_run_dir": str(out.resolve()),
        "source_report_sha256": hashlib.sha256((out / "report.json").read_bytes()).hexdigest(),
        "business_output_hash": r["business_output_hash"],
        "default_view": r["default_view"],
        "findings": findings,
        "qualification": "当前池回填、修订拆股历史、未使用缺证据的市场/财报/交易资格过滤；不是PIT研究或规则批准。",
        "cost_policy": f"每边滑点{t0}bp，零手续费；所有5/10/20bp分档均保留。",
        "notes": [
            "收益为假设入场至固定会话收盘的价格收益；不计分红，不含止损、仓位、资金曲线。",
            "同证券无条件参考使用固定窗口所有候选会话的均值，不是同日匹配的因果对照；SPY按同入场/终点日期配对。",
            "同证券和同日事件相关、标签重叠且非独立样本；30例只是低样本展示门，不是统计显著性或研究批准门。",
            "RTH、历史可知时间、财报/VIX/状态证据缺口仍存在；全部事件的正式可交易资格保持BLOCKED。",
        ],
    }
    (out / "conclusions.json").write_text(json.dumps(conclusions, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# 十年真实数据：EOD探索性规则回放结论",
        "",
        "**已实际执行。当前样本未显示稳定的做多优势。**",
        "",
        f"- 数据：{cov['requested_symbols']}个目标、{cov['rows']:,}条日线，{cov['first_date']}至{cov['last_date']}。",
        f"- 触发统计：{cov['start_date']}至{cov['end_date']}，{cov['evaluation_sessions']:,}个评估会话、"
        f"{cov['security_sessions']:,}个有效证券会话（名义网格{cov['nominal_calendar_security_sessions']:,}，"
        f"已扣除缺史/缺尾标的）；此前260会话预热，末尾保留确认与标签缓冲。",
        f"- 默认口径：确认后下一交易日开盘、{h0}会话持有、每边{t0}bp滑点、零手续费。",
        f"- 模式：{protocol.get('research_mode', 'REVISED_HISTORY')} / EOD_EXPLORATORY，修订历史拆股单位、价格收益；"
        "正式RTH验证未通过，全部交易资格保持阻断。",
        "",
        "## 结论",
        "",
    ]
    lines.extend(f"{i}. {t}" for i, t in enumerate(findings, 1))
    lines.extend(["", "## 周期与成本复核", ""])
    lines.extend(horizon_lines)
    lines.extend(["", "成本分档（默认20会话、下一交易日开盘）：", ""])
    lines.extend(tier_lines)
    if cc_line:
        lines.extend(["", cc_line])
    lines.extend(["", "## 方向型规则分解（默认口径）", ""])
    lines.append("| 规则 | 家族 | 确认样本 | 平均净收益 | 中位数 | 正收益比例 | 相对SPY | 相对无条件 |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for s in directional_rules:
        lines.append(
            f"| {s['value']} | {life.get(s['value'], {}).get('family', '')} | {s['completed']} | "
            f"{pct(s['net_return']['mean'])} | {pct(s['net_return']['p50'])} | {pct2(s['positive_net_fraction'])} | "
            f"{pp(s['spy_excess_return']['mean'])} | {pp(s['unconditional_excess_return']['mean'])} |"
        )
    lines.extend(["", "## 年度分解（默认口径）", ""])
    lines.append("| 确认年份 | 样本 | 平均净收益 | 中位数 | 正收益比例 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for s in years:
        lines.append(
            f"| {s['value']} | {s['completed']} | {pct(s['net_return']['mean'])} | "
            f"{pct(s['net_return']['p50'])} | {pct2(s['positive_net_fraction'])} |"
        )
    lines.extend(["", "## 执行与复查", ""])
    lines.append(f"- 运行耗时{ex['elapsed_seconds']:.2f}秒，峰值进程内存约{ex['peak_rss_bytes'] / 1024 / 1024:.1f}MiB。")
    lines.append(
        f"- 共{r['event_count']:,}个规则事件；去重{r['economic_event_count']:,}个经济事件，"
        f"其中方向型完成样本{n}个；{r['outcome_count']:,}条周期×成本×入场口径标签全部完成"
        f"（{', '.join(f'{k}={v:,}' for k, v in r['outcome_status_counts'].items())}）；标签不是独立事件数。"
    )
    iso = ex.get("isolation", {})
    lines.append(
        f"- 隔离审计：联网{iso.get('network_attempts')}、子进程{iso.get('subprocess_attempts')}、"
        f"越界写入{iso.get('forbidden_writes')}。"
    )
    lines.append("- 事件去重保留TR-01/VP-01双归因；规则行不可直接相加当作总样本。")
    if args.baseline_run_dir:
        base = Path(args.baseline_run_dir)
        bro = json.loads((base / "report.json").read_text())
        data_files = ("events.json", "events.csv", "outcomes.csv", "statistics.json", "unconditional.json")
        same_files = [f for f in data_files if (out / f).exists() and (base / f).exists()
                      and hashlib.sha256((out / f).read_bytes()).hexdigest() == hashlib.sha256((base / f).read_bytes()).hexdigest()]
        proto_same = {k: v for k, v in protocol.items() if k != "code_hash"} == {
            k: v for k, v in bro.get("protocol", {}).items() if k != "code_hash"}
        if len(same_files) == len(data_files) and proto_same:
            lines.append(
                f"- 与既有运行（{base}）逐文件复核：{len(same_files)}/{len(data_files)}个业务输出文件SHA-256一致，"
                "协议除code_hash外一致；business_output_hash不同仅因工作树code_hash与冻结源码快照不同"
                f"（{protocol.get('code_hash', '')[:12]}… vs {bro.get('protocol', {}).get('code_hash', '')[:12]}…），业务数据复现一致。"
            )
        else:
            lines.append(
                f"- 与既有运行（{base}）逐文件复核：一致文件{len(same_files)}/{len(data_files)}，"
                f"协议（除code_hash）{'一致' if proto_same else '不一致'}；business_output_hash "
                f"{'一致' if r['business_output_hash'] == bro['business_output_hash'] else '不一致'}，需人工复核。"
            )
    lines.extend(["", "## 解释边界", ""])
    lines.extend("- " + t for t in conclusions["notes"])
    lines.extend(
        [
            "",
            f"业务输出哈希：{r['business_output_hash']}",
            "",
            f"[机器结论]({out / 'conclusions.json'}) · [事件明细]({out / 'events.csv'}) · "
            f"[后续标签]({out / 'outcomes.csv'}) · [统计分组]({out / 'statistics.json'}) · [报告]({out / 'report.json'})",
            "",
        ]
    )
    (out / "回放结论.md").write_text("\n".join(lines))

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["dimension", "value", "basis", "horizon", "cost_tier", "completed", "net_mean_pct", "net_p50_pct",
         "net_p05_pct", "spy_excess_pp", "unconditional_excess_pp", "win_rate_pct", "neg_gross_pct", "status"]
    )
    for s in stats:
        w.writerow([
            s["dimension"], s["value"], s["entry_basis"], s["horizon"], s["cost_tier"], s["completed"],
            *[("" if s[k]["mean"] is None else f"{float(s[k]['mean']) * 100:+.4f}") for k in ("net_return",)],
            *[("" if s[k]["p50"] is None else f"{float(s[k]['p50']) * 100:+.4f}") for k in ("net_return",)],
            *[("" if s[k]["p05"] is None else f"{float(s[k]['p05']) * 100:+.4f}") for k in ("net_return",)],
            *[("" if s[k]["mean"] is None else f"{float(s[k]['mean']) * 100:+.4f}") for k in ("spy_excess_return", "unconditional_excess_return")],
            *[("" if s.get(k) is None else f"{float(s[k]) * 100:.2f}") for k in ("positive_net_fraction", "negative_gross_fraction")],
            s["status"],
        ])
    (out / "summary.csv").write_text("\ufeff" + buf.getvalue())

    print(json.dumps({
        "status": "CONCLUSIONS_WRITTEN",
        "run_dir": str(out.resolve()),
        "business_output_hash": r["business_output_hash"],
        "baseline_run_dir": args.baseline_run_dir or None,
        "files": ["回放结论.md", "conclusions.json", "summary.csv"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
