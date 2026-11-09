from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from src.services.signal_scanner.api import models as m
from src.services.signal_scanner.core import DomainError, ROOT, Settings, canonical, digest, instant, now, require, stamp, uid
from src.services.signal_scanner.domain.calendar import Calendar
from src.services.signal_scanner.domain.features import adjust
from src.services.signal_scanner.domain.reports import morning_metrics, rolling_statistics
from src.services.signal_scanner.services import accounts, plans
from src.services.signal_scanner.services.auth import Auth
from src.services.signal_scanner.services.config import get_config, initialize, put_config
from src.services.signal_scanner.storage.db import Database, audit, insert, many, one
from src.services.signal_scanner.storage.files import Artifacts
from src.services.signal_scanner.worker.jobs import enqueue
from src.services.signal_scanner.worker.pipeline import latest_workspace, load_events, source_failure


def create_app(settings=None, *, host_auth=None, api_prefix="/api"):
    settings = settings or Settings.load()
    db = Database(settings.db_path)
    db.migrate()
    files = Artifacts(settings.runtime / "artifacts")
    auth = host_auth or Auth(settings)
    cal = Calendar()
    with db.transaction() as cx:
        initialize(cx)
    app = FastAPI(title="交易信号扫描器", version="0.2.0", docs_url=None, redoc_url=None)
    app.state.db = db
    app.state.files = files
    app.state.auth = auth
    app.state.settings = settings

    def envelope(data):
        return dict(request_id=uid("request"), as_of=stamp(), data=data, warnings=[])

    @app.exception_handler(DomainError)
    async def domain_error(request, ex):
        return JSONResponse(
            dict(
                request_id=uid("request"),
                error=dict(
                    code=ex.code, message=ex.message, details=ex.details, retryable=ex.status in (429, 503)
                ),
            ),
            status_code=ex.status,
            headers={"Retry-After": "60"} if ex.status == 429 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, ex):
        malformed = any(e["type"] == "json_invalid" for e in ex.errors())
        return JSONResponse(
            dict(
                request_id=uid("request"),
                error=dict(
                    code="MALFORMED_REQUEST" if malformed else "VALIDATION_ERROR",
                    message="请求字段格式不正确",
                    details=dict(
                        fields=[
                            dict(field=".".join(str(x) for x in e["loc"]), type=e["type"])
                            for e in ex.errors()
                        ]
                    ),
                    retryable=False,
                ),
            ),
            status_code=400 if malformed else 422,
        )

    def authenticated_session(request, cx):
        if host_auth is not None:
            return host_auth.session(request)
        return auth.session(cx, request.cookies.get("scanner_session"))

    @app.middleware("http")
    async def guard(request, call_next):
        if int(request.headers.get("content-length", "0") or "0") > 5 * 1024 * 1024:
            return JSONResponse(
                dict(error=dict(code="PAYLOAD_TOO_LARGE", message="请求上限5MB")), status_code=413
            )
        path = request.scope["path"]
        prefix = request.scope.get("root_path", "")
        if prefix and path.startswith(prefix):
            path = path[len(prefix):]
        try:
            if path.startswith(f"{api_prefix}/") and path != f"{api_prefix}/auth/login":
                with db.read() as cx:
                    session = authenticated_session(request, cx)
                request.state.session = session
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    auth.validate_write(request, session)
            if path == f"{api_prefix}/auth/login" and request.method == "POST":
                require(request.headers.get("origin") == settings.origin, "ORIGIN_DENIED", status=403)
            result = await call_next(request)
            result.headers["X-Content-Type-Options"] = "nosniff"
            result.headers["Referrer-Policy"] = "same-origin"
            result.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; connect-src 'self'"
            )
            if path.startswith(f"{api_prefix}/"):
                result.headers["Cache-Control"] = "no-store"
            return result
        except DomainError as ex:
            return await domain_error(request, ex)

    def mutate(request, payload, fn, *, versioned=False):
        key = request.headers.get("idempotency-key")
        require(key and len(key) <= 200, "MALFORMED_REQUEST", "缺少Idempotency-Key", 400)
        if versioned:
            match = request.headers.get("if-match")
            require(match, "EXPECTED_REVISION_REQUIRED", status=428)
            require(match.strip('"') == str(payload.expected_revision), "REVISION_CONFLICT")
        value = payload.model_dump() if hasattr(payload, "model_dump") else payload
        h = digest(value)
        with db.transaction() as cx:
            authenticated_session(request, cx)
            old = one(
                cx,
                "SELECT * FROM idempotency_requests WHERE actor_id='owner' AND method=? AND route_scope=? AND key=?",
                (request.method, request.url.path, key),
            )
            if old:
                require(old["request_hash"] == h, "IDEMPOTENCY_CONFLICT")
                return old["response_json"]
            result = fn(cx, key)
            insert(
                cx,
                "idempotency_requests",
                dict(
                    id=uid("idem"),
                    actor_id="owner",
                    method=request.method,
                    route_scope=request.url.path,
                    key=key,
                    request_hash=h,
                    state="COMMITTED",
                    http_status=200,
                    response_json=result,
                    expires_at=stamp(now() + timedelta(days=30)),
                ),
            )
        return result

    @app.get("/health/live")
    def live():
        return envelope(dict(status="OK"))

    @app.get("/health/ready")
    def ready():
        with db.read() as cx:
            require(
                cx.execute("PRAGMA user_version").fetchone()[0] == 1, "DEPENDENCY_UNAVAILABLE", status=503
            )
        auth.config()
        return envelope(dict(status="READY", database="OK", schema="OK"))

    @app.post(f"{api_prefix}/auth/login")
    def login(p: m.Login, request: Request):
        require(host_auth is None, "HOST_AUTH_MANAGED", "请使用 DSA 登录", 409)
        with db.transaction() as cx:
            token, result = auth.login(cx, p.password, request.client.host)
        response = JSONResponse(envelope(result))
        response.set_cookie(
            "scanner_session",
            token,
            httponly=True,
            samesite="strict",
            secure=settings.secure_cookie,
            max_age=43200,
            path="/",
        )
        return response

    @app.post(f"{api_prefix}/auth/logout")
    def logout(request: Request):
        require(host_auth is None, "HOST_AUTH_MANAGED", "请使用 DSA 退出登录", 409)
        with db.transaction() as cx:
            cx.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE id=?", (stamp(), request.state.session["id"])
            )
        r = JSONResponse(envelope(dict(authenticated=False)))
        r.delete_cookie("scanner_session")
        return r

    @app.get(f"{api_prefix}/auth/session")
    def session(request: Request):
        if host_auth is not None:
            return envelope(host_auth.public_session(request))
        return envelope(
            dict(
                authenticated=True,
                csrf_token=auth.csrf(request.cookies["scanner_session"]),
                expires_at=request.state.session["expires_at"],
            )
        )

    @app.get(f"{api_prefix}/dashboard")
    def dashboard(action_market_date: str | None = None):
        from src.services.signal_scanner.services.source_data import current_environment, latest_sources

        with db.read() as cx:
            j, space = latest_workspace(cx, files)
            context = cal.context(now(), load_events(cx, stamp()))
            states = many(
                cx,
                "SELECT account_id,scope,origin,status,profile_json FROM account_state WHERE status='ACTIVE'",
            )
            digest_job = one(
                cx,
                "SELECT * FROM job_runs WHERE artifact_kind='DIGEST' AND output_manifest_ref IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            )
            result = dict(
                snapshot_id=j["id"] if j else None,
                state="UNCONFIGURED" if not space else "PARTIAL" if space["partial"] else "READY",
                coverage=space["coverage"] if space else dict(expected=0, ok=0, failed=0),
                partial=space["partial"] if space else True,
                fixture_mode=bool(space and space.get("provider_health", {}).get("feed") == "FICTIONAL_ONLY"),
                source_market_date=space["source_market_date"] if space else None,
                action_market_date=context["action_market_date"],
                risk_market_date=context["risk_market_date"],
                environment=current_environment(space, latest_sources(cx, files)),
                data_as_of=space["data_as_of"] if space else None,
                session_context=space["session_context"] if space else context,
                overnight_checklist=space.get("overnight_checklist", [])
                if space
                else [dict(positions_status="UNKNOWN")],
                accounts=[
                    dict(
                        id=a["account_id"],
                        scope=a["scope"],
                        origin=a["origin"],
                        name=a["profile_json"].get("name", "账户"),
                    )
                    for a in states
                ],
                digest=files.read(digest_job["output_manifest_ref"]) if digest_job else None,
                events_hash=plans.event_hash(cx),
                risk_version=get_config(cx, "risk")["version"],
                jobs=[
                    dict(
                        id=r["id"],
                        job_name=r["job_name"],
                        status=r["status"],
                        created_at=r["created_at"],
                        error=r["error_json"],
                    )
                    for r in many(cx, "SELECT * FROM job_runs ORDER BY created_at DESC LIMIT 8")
                ],
            )
            fault = source_failure(cx)
            if fault and space:
                result.update(
                    state="UNAVAILABLE",
                    partial=True,
                    last_good_coverage=result["coverage"],
                    coverage=dict(
                        expected=space["coverage"]["expected"], ok=0, failed=space["coverage"]["expected"]
                    ),
                    latest_error=fault["error_json"],
                )
        return envelope(result)

    @app.get(f"{api_prefix}/signals")
    def signals(
        view: str = "RESEARCH",
        grade: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        sector: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ):
        require(view in ("PRODUCTION", "RESEARCH") and 1 <= limit <= 100, "OUT_OF_RANGE", status=422)
        with db.read() as cx:
            j, w = latest_workspace(cx, files)
            fh = digest([view, grade, kind, status, sector])
            offset = 0
            if cursor:
                try:
                    import base64

                    token = json.loads(base64.urlsafe_b64decode(cursor + "=" * ((-len(cursor)) % 4)))
                    require(
                        token["snapshot"] == (j["id"] if j else None) and token["filter"] == fh,
                        "CURSOR_STALE",
                    )
                    offset = int(token["offset"])
                    require(offset >= 0, "OUT_OF_RANGE", status=422)
                except (ValueError, KeyError):
                    raise DomainError("MALFORMED_REQUEST", status=400) from None
            items = []
            fault = source_failure(cx)
            for ref in w.get("cards", []) if w else []:
                c = plans.card(cx, ref["card_id"], ref["revision"])
                if (
                    c["view_kind"] != view
                    or (grade and c["effective_grade"] != grade)
                    or (kind and c["kind"] != kind)
                    or (status and c["lifecycle"] != status)
                ):
                    continue
                if not status and c["lifecycle"] not in ("PENDING", "CONFIRMED"):
                    continue
                if ref.get("carried_forward") or fault:
                    c.update(carried_forward=True, availability="BLOCKED")
                    c["reason_codes"] += ["DATA_STALE"]
                if sector:
                    inst = one(cx, "SELECT sector_code FROM instruments WHERE id=?", (c["instrument_id"],))
                    if inst["sector_code"] != sector:
                        continue
                items.append(c)
            items.sort(
                key=lambda c: (
                    {"HIGH": 0, "TRACK": 1, "WATCH": 2, None: 3}[c["effective_grade"]],
                    -(c["quality_score"] or 0),
                    c["id"],
                )
            )
            next_cursor = None
            if offset + limit < len(items):
                import base64

                next_cursor = (
                    base64.urlsafe_b64encode(
                        canonical(dict(snapshot=j["id"], filter=fh, offset=offset + limit)).encode()
                    )
                    .decode()
                    .rstrip("=")
                )
            return envelope(
                dict(
                    items=items[offset : offset + limit],
                    next_cursor=next_cursor,
                    snapshot_id=j["id"] if j else None,
                    total=len(items),
                )
            )

    @app.get(f"{api_prefix}/signals/{{cid}}")
    def detail(cid: str, revision: int | None = None):
        with db.read() as cx:
            result = plans.card(cx, cid, revision)
            j, w = latest_workspace(cx, files)
            if source_failure(cx) or (w and result["instrument_id"] in w.get("failures", {})):
                result.update(availability="BLOCKED", carried_forward=True)
                result["reason_codes"].append("DATA_INCOMPLETE")
            return envelope(result)

    @app.get(f"{api_prefix}/instruments/{{iid}}/bars")
    def bars(iid: str, timeframe: str = "1D", limit: int = 260, snapshot_id: str | None = None):
        require(timeframe == "1D" and 1 <= limit <= 1000, "OUT_OF_RANGE", status=422)
        with db.read() as cx:
            if snapshot_id:
                j = one(cx, "SELECT * FROM job_runs WHERE id=?", (snapshot_id,))
            else:
                j = one(
                    cx,
                    "SELECT * FROM job_runs WHERE input_manifest_ref IS NOT NULL AND published_seq IS NOT NULL ORDER BY published_seq DESC LIMIT 1",
                )
            require(j and j["input_manifest_ref"], "DATA_INCOMPLETE")
            value = files.read(j["input_manifest_ref"])
            require(iid in value["bars"], "RESOURCE_NOT_FOUND", status=404)
            corrected = adjust(
                value["bars"][iid],
                [e for e in value["events"] if e["instrument_id"] == iid],
                value["cutoff_at"],
            )
        return envelope(
            dict(
                items=corrected[-limit:],
                next_cursor=None,
                price_basis="ADJUSTED",
                snapshot_id=j["id"],
                adjustment_epoch=digest(value["events"]),
            )
        )

    @app.post(f"{api_prefix}/signals/{{cid}}/view")
    def view(cid: str, p: m.View, request: Request):
        def fn(cx, key):
            plans.card(cx, cid, p.revision)
            old = one(
                cx, "SELECT * FROM audit_events WHERE action='DISPLAYED' AND object_id=?", (p.view_event_id,)
            )
            if not old:
                audit(cx, "DISPLAYED", p.view_event_id, "仅记录展示", card_id=cid, revision=p.revision)
            return dict(displayed_at=old["occurred_at"] if old else stamp(), recorded=True)

        return envelope(mutate(request, p, fn))

    @app.get(f"{api_prefix}/preflight")
    def preflight(action_market_date: str):
        with db.read() as cx:
            return envelope(plans.preflight(cx, action_market_date))

    @app.put(f"{api_prefix}/preflight")
    def set_preflight(p: m.Preflight, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.set_preflight(cx, p)))

    @app.post(f"{api_prefix}/stop-sessions")
    def open_stop(p: m.StopOpen, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.open_stop(cx, p)))

    @app.patch(f"{api_prefix}/stop-sessions/{{sid}}")
    def advance_stop(sid: str, p: m.StopStep, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.stop_step(cx, sid, p), versioned=True))

    @app.post(f"{api_prefix}/decisions")
    def decision(p: m.Decision, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.decision(cx, p, k)))

    @app.post(f"{api_prefix}/risk/preview")
    def risk_preview(p: m.RiskPreview, request: Request):
        with db.read() as cx:
            return envelope(plans.risk_preview(cx, p, cal, files))

    @app.post(f"{api_prefix}/plans")
    def new_plan(p: m.Draft, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.draft(cx, p)))

    @app.patch(f"{api_prefix}/plans/{{pid}}")
    def patch_plan(pid: str, p: m.PlanPatch, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.patch(cx, pid, p), versioned=True))

    @app.post(f"{api_prefix}/plans/{{pid}}/reserve")
    def reserve(pid: str, p: m.Reserve, request: Request):
        return envelope(
            mutate(request, p, lambda cx, k: plans.reserve(cx, pid, p, cal, files, k), versioned=True)
        )

    @app.post(f"{api_prefix}/plans/{{pid}}/cancel")
    def cancel(pid: str, p: m.Cancel, request: Request):
        return envelope(mutate(request, p, lambda cx, k: plans.cancel(cx, pid, p), versioned=True))

    @app.post(f"{api_prefix}/plans/{{pid}}/export")
    def export(pid: str, p: m.Export, request: Request):
        def fn(cx, key):
            plan = plans.get_plan(cx, pid)
            require(plan["row_version"] == p.expected_revision, "REVISION_CONFLICT")
            if p.basis != "HISTORICAL":
                require(plan["status"] == "RESERVED", "SIGNAL_INVALID")
                result = plans.risk_preview(
                    cx,
                    m.RiskPreview(
                        account_id=plan["account_id"],
                        entry=plan["entry_ref"],
                        stop=plan["stop_ref"],
                        card_id=plan["card_id"],
                        card_revision=plan["card_revision"],
                        action_market_date=plan["action_market_date"],
                    ),
                    cal,
                    files,
                    exclude_plan=pid,
                )
                require(result["status"] == "CALCULATED", "RISK_LOCKED", details=result)
                require(
                    result["policy_version"] == plan["risk_profile_version"],
                    "REVISION_CONFLICT",
                    "风险参数已变化，请重新审阅计划",
                )
                require(
                    int(result["qty"]) >= plan["qty_remaining"],
                    "RISK_LOCKED",
                    "当前预算已不足以覆盖原计划数量，请取消并重建计划",
                )
            eid = uid("export")
            artifact = dict(
                kind="PLAN_EXPORT",
                plan_id=pid,
                revision=plan["row_version"],
                basis=p.basis,
                format=p.format,
                plan={k: v for k, v in plan.items() if k not in ("idempotency_key", "risk_trace_json")},
                order_status="NOT_EXECUTED",
                generated_at=stamp(),
            )
            ref = files.save(artifact, "exports")
            insert(
                cx,
                "job_runs",
                dict(
                    id=eid,
                    logical_key=eid,
                    revision=1,
                    job_name="EXPORT",
                    artifact_kind="EXPORT",
                    status="SUCCEEDED",
                    output_manifest_ref=ref,
                ),
            )
            return dict(
                export_id=eid,
                content_type="text/markdown" if p.format == "markdown" else "text/csv",
                download_url=f"{api_prefix}/exports/" + eid,
                basis=p.basis,
            )

        return envelope(mutate(request, p, fn, versioned=True))

    @app.get(f"{api_prefix}/exports/{{eid}}")
    def download(eid: str):
        with db.read() as cx:
            j = one(
                cx,
                "SELECT * FROM job_runs WHERE id=? AND artifact_kind='EXPORT' AND status='SUCCEEDED'",
                (eid,),
            )
            require(j, "RESOURCE_NOT_FOUND", status=404)
        artifact = files.read(j["output_manifest_ref"])
        if artifact["kind"] == "PRIVACY_EXPORT":
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("personal-data.json", canonical(artifact))
            return Response(
                output.getvalue(),
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{eid}.zip"'},
            )
        if artifact["format"] == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["字段", "值"])
            for k, v in artifact["plan"].items():
                s = canonical(v) if isinstance(v, (list, dict)) else str(v)
                if s.lstrip().startswith(("=", "+", "-", "@")):
                    s = "'" + s
                writer.writerow([k, s])
            writer.writerow(["订单状态", "未执行"])
            content = output.getvalue()
            mime = "text/csv"
        else:
            content = "# 计划参考参数\n\n未执行订单；未验证券商保护。\n\n" + "\n".join(
                f"- {k}: {canonical(v) if isinstance(v, (dict, list)) else v}"
                for k, v in artifact["plan"].items()
            )
            mime = "text/markdown"
        return Response(
            content,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{eid}.{"csv" if mime == "text/csv" else "md"}"'
            },
        )

    @app.get(f"{api_prefix}/accounts/{{aid}}/risk")
    def account_risk(aid: str):
        with db.read() as cx:
            a, s = accounts.snapshot(cx, aid)
            pending = many(cx, "SELECT * FROM plans WHERE account_id=? ORDER BY created_at DESC", (aid,))
            return envelope(
                dict(
                    account_id=aid,
                    scope=a["scope"],
                    origin=a["origin"],
                    status="KNOWN"
                    if s["completeness"] == "COMPLETE" and a["confirmed_ledger_head"] == s["ledger_head_ref"]
                    else "UNKNOWN",
                    snapshot=s,
                    locks=a["locks_json"],
                    recovery_remaining=a["recovery_remaining"],
                    plans=[{**p, "revision": p["row_version"]} for p in pending],
                    snapshot_id=s["id"],
                    ledger_head=s["ledger_head_ref"],
                )
            )

    @app.post(f"{api_prefix}/accounts")
    def account_new(p: m.Account, request: Request):
        return envelope(mutate(request, p, lambda cx, k: accounts.create_account(cx, p, cal)))

    @app.post(f"{api_prefix}/accounts/{{aid}}/confirm")
    def account_confirm(aid: str, p: m.Confirm, request: Request):
        return envelope(mutate(request, p, lambda cx, k: accounts.confirm(cx, aid, p, cal)))

    @app.post(f"{api_prefix}/ledger/import")
    def ledger_import(p: m.LedgerImport, request: Request):
        return envelope(mutate(request, p, lambda cx, k: accounts.import_entries(cx, p, cal)))

    @app.post(f"{api_prefix}/ledger/{{eid}}/reverse")
    def reverse(eid: str, p: m.Reverse, request: Request):
        return envelope(mutate(request, p, lambda cx, k: accounts.reverse_entry(cx, eid, p, cal)))

    @app.get(f"{api_prefix}/reviews")
    def reviews(
        request: Request,
        section: str = "SUMMARY",
        dataset: str = "SIGNAL_OUTCOME",
        account_id: str | None = None,
        horizon: int = 10,
        rolling_window: str = "ALL",
        limit: int = 50,
    ):
        require(
            section in ("SUMMARY", "DECISIONS", "LEDGER", "OUTCOMES")
            and rolling_window in ("ALL", "50", "100")
            and 1 <= limit <= 100,
            "OUT_OF_RANGE",
            status=422,
        )
        import base64

        cursor = request.query_params.get("cursor")
        cursor_value = None
        if cursor:
            try:
                cursor_value = json.loads(base64.urlsafe_b64decode(cursor + "=" * ((-len(cursor)) % 4)))
            except (ValueError, TypeError):
                raise DomainError("MALFORMED_REQUEST", "分页游标无效", 400) from None
        if cursor_value is not None:
            require(
                isinstance(cursor_value, dict)
                and set(cursor_value) >= {"end", "start", "cutoff", "filter_hash", "snapshot_id", "offset"},
                "MALFORMED_REQUEST",
                status=400,
            )
        end = stamp(instant(request.query_params.get("to", cursor_value["end"] if cursor_value else stamp())))
        start = stamp(
            instant(
                request.query_params.get(
                    "from", cursor_value["start"] if cursor_value else stamp(now() - timedelta(days=90))
                )
            )
        )
        require(instant(start) < instant(end), "OUT_OF_RANGE", status=422)
        cutoff = stamp(
            instant(
                request.query_params.get("report_cutoff", cursor_value["cutoff"] if cursor_value else stamp())
            )
        )
        require(instant(cutoff) <= now(), "OUT_OF_RANGE", status=422)
        with db.read() as cx:
            if section in ("LEDGER", "DECISIONS"):
                if section == "LEDGER":
                    require(account_id, "VALIDATION_ERROR", status=422)
                    items = many(
                        cx,
                        "SELECT * FROM ledger_entries WHERE account_id=? AND occurred_at>=? AND occurred_at<? AND created_at<=? ORDER BY occurred_at DESC,id",
                        (account_id, start, end, cutoff),
                    )
                else:
                    items = many(
                        cx,
                        "SELECT * FROM decisions WHERE server_time>=? AND server_time<? AND created_at<=? ORDER BY server_time DESC,id",
                        (start, end, cutoff),
                    )
                filter_hash = digest([section, account_id, start, end, cutoff])
                privacy = one(
                    cx,
                    "SELECT id FROM audit_events WHERE action='PRIVACY_DELETED' ORDER BY occurred_at DESC LIMIT 1",
                )
                snapshot_id = digest([filter_hash, [(r["id"], r["created_at"]) for r in items], privacy])
                offset = 0
                if cursor_value:
                    require(
                        cursor_value.get("filter_hash") == filter_hash
                        and cursor_value.get("snapshot_id") == snapshot_id,
                        "CURSOR_STALE",
                    )
                    offset = cursor_value.get("offset", -1)
                    require(isinstance(offset, int) and offset >= 0, "MALFORMED_REQUEST", status=400)
                next_cursor = None
                if offset + limit < len(items):
                    next_cursor = (
                        base64.urlsafe_b64encode(
                            canonical(
                                dict(
                                    filter_hash=filter_hash,
                                    snapshot_id=snapshot_id,
                                    offset=offset + limit,
                                    start=start,
                                    end=end,
                                    cutoff=cutoff,
                                )
                            ).encode()
                        )
                        .decode()
                        .rstrip("=")
                    )
                return envelope(
                    dict(
                        items=items[offset : offset + limit],
                        next_cursor=next_cursor,
                        snapshot_id=snapshot_id,
                        total=len(items),
                    )
                )
            pref = get_config(cx, "preferences")
            if dataset == "USER_DECISIONS":
                require(
                    section == "SUMMARY" and not account_id and rolling_window == "ALL",
                    "OUT_OF_RANGE",
                    status=422,
                )
                ds = many(cx, "SELECT * FROM decisions WHERE server_time<=?", (cutoff,))
                return envelope(
                    dict(
                        dataset=dataset,
                        process_metrics=morning_metrics(
                            ds, start, min(end, cutoff), pref["value"]["morning_window"], pref["version"]
                        ),
                    )
                )
            records = []
            if dataset in ("PAPER_STRATEGY", "MANUAL_RESULT"):
                require(account_id, "VALIDATION_ERROR", "需要明确账户", 422)
                a = accounts.account(cx, account_id)
                require(
                    (dataset == "PAPER_STRATEGY" and a["scope"] == "PAPER")
                    or (dataset == "MANUAL_RESULT" and a["scope"] == "MANUAL"),
                    "OUT_OF_RANGE",
                    status=422,
                )
                derived = accounts.derive(cx, a, instant(cutoff), knowledge_cutoff=instant(cutoff))
                records = [
                    {
                        **r,
                        "dataset": dataset,
                        "costs_version": "ACTUAL_RECORDED" if a["scope"] == "MANUAL" else "costs-v1",
                        "data_mode": "SELF_REPORTED" if a["scope"] == "MANUAL" else "SIMULATED",
                        "status": "UNRESOLVED"
                        if not a["profile_json"].get("funds_complete") or derived["errors"]
                        else r["status"],
                        "reason_code": "ACCOUNT_FACTS_INCOMPLETE"
                        if not a["profile_json"].get("funds_complete") or derived["errors"]
                        else r.get("reason_code"),
                    }
                    for r in derived["episodes"]
                    if start <= r["closed_at"] < end
                ]
            elif dataset == "SIGNAL_OUTCOME":
                require(horizon in (5, 10, 20), "OUT_OF_RANGE", status=422)
                report = one(
                    cx,
                    "SELECT * FROM job_runs WHERE job_name IN ('REPLAY','PAPER') AND status='SUCCEEDED' AND output_manifest_ref IS NOT NULL AND updated_at<=? ORDER BY updated_at DESC LIMIT 1",
                    (cutoff,),
                )
                if report:
                    records = [
                        r
                        for r in files.read(report["output_manifest_ref"]).get("outcomes", [])
                        if r["horizon"] == horizon and start <= r["confirmed_at"] < end
                    ]
            else:
                require(False, "OUT_OF_RANGE", status=422)
            filters = {
                key: request.query_params.get(key)
                for key in (
                    "rule_id",
                    "rule_version",
                    "experiment_version",
                    "costs_version",
                    "origin",
                    "data_mode",
                )
                if request.query_params.get(key) is not None
            }
            if filters:
                records = [
                    r for r in records if all(str(r.get(key)) == value for key, value in filters.items())
                ]
            return envelope(
                dict(
                    dataset=dataset,
                    status="COMPUTED" if records else "NOT_COMPUTED",
                    reason_code=None if records else "NO_SAMPLES",
                    groups=rolling_statistics(records, rolling_window),
                    filter_hash=digest(
                        dict(
                            dataset=dataset,
                            account=account_id,
                            horizon=horizon,
                            window=rolling_window,
                            start=start,
                            end=end,
                            filters=filters,
                        )
                    ),
                    cutoff=cutoff,
                    from_at=start,
                    to=end,
                    timezone=pref["value"]["display_timezone"],
                )
            )

    @app.get(f"{api_prefix}/screen")
    def screen():
        from src.services.signal_scanner.services.screen import screen_data

        with db.read() as cx:
            return envelope(screen_data(cx, files, cal))

    @app.get(f"{api_prefix}/data-status")
    def data_status():
        from src.services.signal_scanner.services.source_data import latest_sources

        with db.read() as cx:
            source = get_config(cx, "provider")
            published, workspace = latest_workspace(cx, files)
            heart = settings.runtime / "worker-heartbeat.json"
            worker_state = "UNKNOWN"
            if heart.is_file():
                try:
                    worker_state = (
                        "OK"
                        if instant(json.loads(heart.read_text())["at"]) > now() - timedelta(seconds=120)
                        else "STALE"
                    )
                except (ValueError, KeyError):
                    worker_state = "UNKNOWN"
            weekly = one(
                cx,
                "SELECT * FROM job_runs WHERE job_name='WEEKLY_REPORT' AND output_manifest_ref IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            )
            return envelope(
                dict(
                    worker=worker_state,
                    sources=latest_sources(cx, files),
                    provider="DISABLED" if source["value"]["mode"] == "DISABLED" else "UNVERIFIED",
                    capabilities=source["value"]["capabilities"],
                    health=workspace["provider_health"]
                    if workspace
                    else dict(
                        connection="DISABLED" if source["value"]["mode"] == "DISABLED" else "UNKNOWN",
                        permission="UNKNOWN",
                        freshness="UNKNOWN",
                        quality="UNKNOWN",
                    ),
                    source_config=source,
                    latest_data_health_report_ref=weekly["id"] if weekly else None,
                    data_health=files.read(weekly["output_manifest_ref"]) if weekly else None,
                    jobs=[
                        {
                            k: r[k]
                            for k in ("id", "job_name", "status", "created_at", "error_json", "progress_json")
                        }
                        for r in many(cx, "SELECT * FROM job_runs ORDER BY created_at DESC LIMIT 20")
                    ],
                )
            )

    @app.get(f"{api_prefix}/jobs/{{jid}}")
    def job_status(jid: str):
        with db.read() as cx:
            r = one(cx, "SELECT * FROM job_runs WHERE id=?", (jid,))
            require(r, "RESOURCE_NOT_FOUND", status=404)
        return envelope({k: v for k, v in r.items() if k not in ("input_manifest_ref", "lease_owner")})

    @app.post(f"{api_prefix}/scans/retry")
    def retry(p: m.Retry, request: Request):
        def fn(cx, key):
            old = one(
                cx,
                "SELECT * FROM job_runs WHERE logical_key=? ORDER BY revision DESC LIMIT 1",
                (p.logical_key,),
            )
            require(old, "RESOURCE_NOT_FOUND", status=404)
            require(old["job_name"] in ("DAILY_SCAN", "VERIFY", "STATUS"), "OUT_OF_RANGE", status=422)
            jid = enqueue(
                cx,
                old["job_name"],
                old["logical_key"] + ":manual:" + key,
                old["market_date"],
                dict(reason=p.reason),
                True,
            )
            return dict(job_id=jid, id=jid, status="QUEUED", replay=True)

        return envelope(mutate(request, p, fn))

    @app.get(f"{api_prefix}/configs/{{key}}")
    def config(key: str):
        with db.read() as cx:
            return envelope(get_config(cx, key))

    @app.put(f"{api_prefix}/configs/{{key}}")
    def config_update(key: str, p: m.Configuration, request: Request):
        return envelope(mutate(request, p, lambda cx, k: put_config(cx, key, p, cal, files)))

    @app.post(f"{api_prefix}/notifications/{{nid}}/resend")
    def resend(nid: str, p: m.Note, request: Request):
        def fn(cx, key):
            old = one(cx, "SELECT * FROM notifications WHERE id=?", (nid,))
            require(old, "RESOURCE_NOT_FOUND", status=404)
            require(old["status"] in ("UNKNOWN", "FAILED", "ACCEPTED"), "OUT_OF_RANGE", status=422)
            new = uid("notification")
            insert(
                cx,
                "notifications",
                dict(
                    id=new,
                    artifact_id=old["artifact_id"],
                    artifact_revision=old["artifact_revision"],
                    recipient_id=old["recipient_id"],
                    channel=old["channel"],
                    message_type="MANUAL_RESEND:" + key,
                    status="PENDING",
                    delivery_id=uid("delivery"),
                    expires_at=stamp(now() + timedelta(minutes=30)),
                    manual_resend_of=nid,
                ),
            )
            audit(cx, "NOTIFICATION_RESEND", nid, p.reason, possible_duplicate=True)
            return dict(id=new, status="PENDING", manual_resend_of=nid)

        return envelope(mutate(request, p, fn))

    @app.post(f"{api_prefix}/user-pause")
    def pause(p: m.Pause, request: Request):
        def fn(cx, key):
            old = get_config(cx, "preferences")
            value = {**old["value"], "paused": p.paused}
            put_config(
                cx,
                "preferences",
                m.Configuration(expected_version=old["version"], change_reason=p.reason, value=value),
                cal,
                files,
            )
            return dict(paused=p.paused, risk_information_visible=True)

        return envelope(mutate(request, p, fn))

    @app.post(f"{api_prefix}/research/runs")
    def research(p: m.Research, request: Request):
        def fn(cx, key):
            require(
                p.experiment_config_version == get_config(cx, "experiment")["version"], "REVISION_CONFLICT"
            )
            jid = enqueue(
                cx,
                "REPLAY",
                "RESEARCH:" + key,
                cal.last_completed(now()),
                dict(mode=p.mode, experiment_config_version=p.experiment_config_version),
            )
            return dict(id=jid, status="QUEUED")

        return envelope(mutate(request, p, fn))

    @app.post(f"{api_prefix}/privacy/export")
    def privacy_export(p: m.PrivacyExport, request: Request):
        return envelope(
            mutate(
                request,
                p,
                lambda cx, k: dict(
                    job_id=enqueue(cx, "PRIVACY_EXPORT", "PRIVACY_EXPORT:" + k, progress=p.model_dump()),
                    status="QUEUED",
                ),
            )
        )

    @app.post(f"{api_prefix}/privacy/delete")
    def privacy_delete(p: m.PrivacyDelete, request: Request):
        def fn(cx, key):
            require(
                one(
                    cx,
                    "SELECT id FROM job_runs WHERE id=? AND artifact_kind='EXPORT' AND status='SUCCEEDED'",
                    (p.export_id,),
                ),
                "DATA_INCOMPLETE",
                "先完成导出",
            )
            if p.scope == "ACCOUNT_DATA":
                require(p.account_id, "VALIDATION_ERROR", status=422)
                accounts.account(cx, p.account_id)
            return dict(
                job_id=enqueue(cx, "PRIVACY_DELETE", "PRIVACY_DELETE:" + key, progress=p.model_dump()),
                status="QUEUED",
                backup_policy="ROTATION_APPLIES",
            )

        return envelope(mutate(request, p, fn))

    @app.post(f"{api_prefix}/batch-history/start")
    def start_batch_history(p: m.BatchHistoryFetch, request: Request):
        def fn(cx, key):
            from src.services.signal_scanner.services.batch_history import load_universe_csv

            csv_path = settings.runtime / "imports" / p.csv_filename
            universe = load_universe_csv(csv_path)

            require(
                len(universe) <= 100,
                "OUT_OF_RANGE",
                "批量拉取最多支持100只标的",
                422,
            )

            end_date = cal.last_completed(now())
            start_date = cal.shift(end_date, -(p.years * 252))

            logical_key = f"BATCH_HISTORY:{p.csv_filename}:{p.years}y"

            jid = enqueue(
                cx,
                "BATCH_HISTORY",
                logical_key,
                end_date,
                progress={
                    "csv_filename": p.csv_filename,
                    "years": p.years,
                    "total": len(universe),
                    "processed": 0,
                    "current_ticker": None,
                    "results": [],
                    "started_at": stamp(),
                },
            )

            return {
                "job_id": jid,
                "status": "QUEUED",
                "total_symbols": len(universe),
                "start_date": start_date,
                "end_date": end_date,
            }

        return envelope(mutate(request, p, fn))

    @app.get(f"{api_prefix}/batch-history/status")
    def batch_history_status(job_id: str | None = None):
        with db.read() as cx:
            if job_id:
                job = one(cx, "SELECT * FROM job_runs WHERE id=? AND job_name='BATCH_HISTORY'", (job_id,))
                require(job, "RESOURCE_NOT_FOUND", "作业不存在", 404)
            else:
                job = one(
                    cx,
                    "SELECT * FROM job_runs WHERE job_name='BATCH_HISTORY' ORDER BY created_at DESC LIMIT 1",
                )
                require(job, "RESOURCE_NOT_FOUND", "无批量拉取作业", 404)

            progress = job["progress_json"]
            total = progress.get("total", 0)
            processed = progress.get("processed", 0)
            results = progress.get("results", [])

            succeeded = sum(1 for r in results if r.get("status") == "SUCCEEDED")
            failed = sum(1 for r in results if r.get("status") == "FAILED")
            skipped = sum(1 for r in results if r.get("status") == "SKIPPED")
            insufficient = sum(1 for r in results if r.get("status") == "INSUFFICIENT")

            elapsed_seconds = None
            remaining_seconds = None
            if progress.get("started_at"):
                from src.services.signal_scanner.core import instant
                started = instant(progress["started_at"])
                elapsed = (now() - started).total_seconds()
                elapsed_seconds = int(elapsed)
                if processed > 0 and processed < total:
                    avg_per_symbol = elapsed / processed
                    remaining_seconds = int(avg_per_symbol * (total - processed))

            return dict(
                job_id=job["id"],
                status=job["status"],
                created_at=job["created_at"],
                total=total,
                processed=processed,
                current_ticker=progress.get("current_ticker"),
                succeeded=succeeded,
                failed=failed,
                skipped=skipped,
                insufficient=insufficient,
                elapsed_seconds=elapsed_seconds,
                remaining_seconds=remaining_seconds,
                results=results,
            )

    @app.get(f"{api_prefix}/batch-history/report")
    def batch_history_report(job_id: str):
        with db.read() as cx:
            job = one(cx, "SELECT * FROM job_runs WHERE id=? AND job_name='BATCH_HISTORY'", (job_id,))
            require(job, "RESOURCE_NOT_FOUND", "作业不存在", 404)
            require(
                job["output_manifest_ref"],
                "RESOURCE_NOT_FOUND",
                "报告尚未生成",
                404,
            )

            report = files.read(job["output_manifest_ref"])
            return report

    @app.get(f"{api_prefix}/market-context")
    def market_context(
        at: str | None = None,
        display_timezone: str = "Asia/Singapore",
        market: str = "US_EQUITIES",
        account_id: str | None = None,
    ):
        moment = instant(at) if at else now()
        require(moment <= now() and market == "US_EQUITIES", "VALIDATION_ERROR", status=422)
        try:
            ZoneInfo(display_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise DomainError("VALIDATION_ERROR", "非法时区", 422) from None
        if not "1990-01-02" <= moment.date().isoformat() <= "2035-12-31":
            return envelope(
                dict(
                    evaluated_at=stamp(moment),
                    market=market,
                    session_status="UNKNOWN",
                    rth_phase=None,
                    calendar_version=cal.version,
                    next_events=[],
                    event_coverage=dict(status="UNKNOWN"),
                    reason_code="CALENDAR_OUT_OF_RANGE",
                )
            )
        with db.read() as cx:
            if account_id:
                accounts.account(cx, account_id)
            events = load_events(cx, stamp(moment))
            event_config = get_config(cx, "events")
            return envelope(
                cal.context(
                    moment,
                    events,
                    display_timezone,
                    coverage=dict(
                        status=event_config["value"]["coverage"],
                        verified_at=event_config["value"]["verified_at"],
                    ),
                )
            )

    from src.services.signal_scanner.api.rule_validation import router as rule_validation_router

    app.include_router(rule_validation_router(settings, api_prefix=api_prefix))

    dist = ROOT / "frontend" / "dist"
    if host_auth is None and dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            require(not path.startswith(("api/", "health/")), "RESOURCE_NOT_FOUND", status=404)
            return FileResponse(dist / "index.html")

    return app
