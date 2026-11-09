import json
import os
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from src.services.signal_scanner.core import require, stamp, uid
from src.services.signal_scanner.services.rule_validation_web import models, query, service
from src.services.signal_scanner.services.rule_validation_web.catalog import default_draft
from src.services.signal_scanner.services.rule_validation_web.exports import artifact
from src.services.signal_scanner.services.rule_validation_web.store import Store, public


def router(settings, *, api_prefix="/api"):
    def enabled():
        require(
            os.environ.get("RULE_VALIDATION_WEB_ENABLED", "true").lower() == "true",
            "FEATURE_DISABLED",
            "规则验证未启用",
            404,
        )

    api = APIRouter(prefix=f"{api_prefix}/rule-validation", dependencies=[Depends(enabled)])
    store = Store.configured(settings.runtime)

    def envelope(data):
        require(
            os.environ.get("RULE_VALIDATION_WEB_ENABLED", "true").lower() == "true",
            "FEATURE_DISABLED",
            "规则验证未启用",
            404,
        )
        return {
            "request_id": uid("request"),
            "as_of": stamp(),
            "data": {"api_schema_version": "1", **data},
            "warnings": [],
        }

    def write(request, p, fn):
        require(
            os.environ.get("RULE_VALIDATION_WEB_ENABLED", "true").lower() == "true",
            "FEATURE_DISABLED",
            status=404,
        )
        return envelope(store.mutate(request.url.path, request.headers.get("idempotency-key"), p, fn))

    def filters(request, exclude=()):
        require(
            len(request.query_params) == len(set(request.query_params.keys())), "DUPLICATE_FILTER", status=422
        )
        return {k: v for k, v in request.query_params.items() if k not in exclude}

    def run(id):
        return store.get("run", id)

    @api.get("/catalog")
    def catalog(limit: int = 50, cursor: int = 0):
        require(1 <= limit <= 200 and cursor >= 0, "INVALID_PAGINATION", status=422)
        datasets = [
            dict(**public(d), default_draft=default_draft(d)) for d in store.all("dataset") if d["active"]
        ]
        return envelope(
            {
                "datasets": datasets[cursor : cursor + limit],
                "runtime_ready": store.ready,
                "next_cursor": cursor + limit if cursor + limit < len(datasets) else None,
                "api_schema_version": "1",
            }
        )

    @api.post("/validations", status_code=202)
    def validate(p: models.ValidationRequest, request: Request):
        p = p.model_dump(exclude_none=True)
        return write(request, p, lambda cx: service.create_validation(store, p, cx))

    @api.get("/validations/{id}")
    def validation(id: str):
        return envelope(public(store.get("validation", id)))

    @api.post("/runs", status_code=202)
    def start(p: models.RunRequest, request: Request):
        p = p.model_dump(exclude_none=True)
        return write(request, p, lambda cx: service.create_run(store, p, cx))

    @api.get("/runs")
    def runs(
        request: Request,
        limit: int = 50,
        cursor: int = 0,
        status: str | None = None,
        data_kind: str | None = None,
        search: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
    ):
        require(
            not filters(
                request, ("limit", "cursor", "status", "data_kind", "search", "start_date", "end_date")
            ),
            "UNSUPPORTED_FILTER",
            status=422,
        )
        require(1 <= limit <= 200 and cursor >= 0, "INVALID_PAGINATION", status=422)
        require(
            status
            in (
                None,
                "QUEUED",
                "RUNNING",
                "CANCEL_REQUESTED",
                "CANCELLED",
                "SUCCEEDED",
                "PARTIAL",
                "FAILED",
                "SKIPPED",
            ),
            "INVALID_FILTER",
            status=422,
        )
        require(data_kind in (None, "REAL", "SYNTHETIC"), "INVALID_FILTER", status=422)
        require(not start_date or not end_date or start_date <= end_date, "INVALID_DATE_RANGE", status=422)
        rows = sorted(store.all("run"), key=lambda r: (r["created_at"], r["id"]), reverse=True)
        rows = [
            r
            for r in rows
            if (not status or r["status"] == status)
            and (not data_kind or r["data_kind"] == data_kind)
            and search.casefold() in r["name"].casefold()
            and (not start_date or (r.get("start_date") and r["start_date"] >= str(start_date)))
            and (not end_date or (r.get("end_date") and r["end_date"] <= str(end_date)))
        ]
        return envelope(
            {
                "items": [service.run_status(store, r) for r in rows[cursor : cursor + limit]],
                "next_cursor": cursor + limit if cursor + limit < len(rows) else None,
                "total": len(rows),
            }
        )

    @api.get("/runs/{id}")
    def detail(id: str):
        return envelope(service.run_status(store, run(id)))

    @api.post("/runs/{id}/retry", status_code=202)
    def retry(id: str, p: models.RetryRequest, request: Request):
        p = p.model_dump(exclude_none=True)
        return write(request, p, lambda cx: service.retry(store, id, p, cx))

    @api.post("/runs/{id}/cancel", status_code=202)
    def cancel(id: str, p: models.CancelRequest, request: Request):
        p = p.model_dump()
        return write(request, p, lambda cx: service.cancel(store, id, p, cx))

    @api.get("/runs/{id}/variants")
    def variant_plan(id: str, cursor: int = 0, limit: int = 30, validation_cursor: int = 0):
        from src.services.signal_scanner.services.rule_validation_web.variants import plan

        return envelope(plan(store, id, cursor, limit, validation_cursor))

    @api.post("/runs/{id}/variants/validations", status_code=202)
    def variant_validate(id: str, p: models.VariantRequest, request: Request):
        from src.services.signal_scanner.services.rule_validation_web.variants import create_validation

        p = p.model_dump()
        return write(request, p, lambda cx: create_validation(store, id, p, cx))

    @api.get("/probes")
    def probes(cursor: int = 0, limit: int = 30):
        from src.services.signal_scanner.services.rule_validation_web.probes import history

        return envelope(history(settings, cursor, limit))

    @api.post("/runs/{id}/reveal", status_code=202)
    def reveal(id: str, p: models.RevealRequest, request: Request):
        p = p.model_dump(exclude_none=True)
        return write(request, p, lambda cx: service.reveal(store, id, p, cx))

    @api.get("/runs/{id}/report")
    def report(id: str):
        return envelope(query.summary(run(id)))

    @api.get("/runs/{id}/rows")
    def rows(
        id: str,
        request: Request,
        section: str = "events",
        limit: int = 50,
        cursor: int = 0,
        event_scope: str = "all",
    ):
        return envelope(
            query.page(
                run(id),
                section,
                filters(request, ("section", "limit", "cursor", "event_scope")),
                cursor,
                limit,
                event_scope,
            )
        )

    @api.get("/runs/{id}/statistics")
    def statistics(id: str, request: Request, dimension: str = "ECONOMIC_TOTAL"):
        return envelope(query.statistics(run(id), filters(request, ("dimension",)), dimension))

    @api.get("/runs/{id}/benchmarks")
    def benchmarks(id: str, request: Request, kind: str = "SPY", instrument_id: str | None = None):
        excluded = ("kind", "instrument_id") if kind == "UNCONDITIONAL" else ("kind",)
        return envelope(query.benchmark(run(id), filters(request, excluded), kind, instrument_id))

    @api.get("/runs/{id}/events/{event_id}")
    def event(id: str, event_id: str):
        return envelope(query.event_detail(run(id), event_id))

    @api.get("/runs/{id}/events/{event_id}/chart")
    def event_chart(id: str, event_id: str, as_of: str = "TRIGGER", window: int = 60):
        from src.services.signal_scanner.services.rule_validation_web.charts import event_chart

        return envelope(event_chart(run(id), event_id, as_of, window))

    @api.get("/runs/{id}/artifacts")
    def artifacts(id: str):
        r = run(id)
        with query.connect(r):
            pass
        return envelope(
            {"items": r.get("artifacts", []), "integrity": "VERIFIED", "report_hash": r["report_hash"]}
        )

    @api.get("/runs/{id}/artifacts/{name}")
    def download(id: str, name: str):
        stream, metadata = artifact(store, run(id), name)
        media = {"json": "application/json", "md": "text/markdown", "csv": "text/csv"}[name.rsplit(".", 1)[1]]

        def chunks():
            try:
                while block := stream.read(65536):
                    yield block
            finally:
                stream.close()

        return StreamingResponse(
            chunks(),
            media_type=media,
            headers={
                "Content-Disposition": f'attachment; filename="derived-{name}"',
                "Content-Length": str(metadata["size_bytes"]),
                "X-Source-Business-Hash": metadata["source_business_hash"],
                "X-Artifact-SHA256": metadata["sha256"],
            },
        )

    @api.post("/comparisons", status_code=202)
    def compare(p: models.ComparisonRequest, request: Request):
        p = p.model_dump(exclude_none=True)
        return write(request, p, lambda cx: service.create_comparison(store, p, cx))

    @api.get("/comparisons/{id}")
    def comparison(id: str):
        return envelope(public(store.get("comparison", id)))

    @api.get("/comparisons/{id}/changes")
    def changes(id: str, kind: str = "added", cursor: int = 0, limit: int = 50):
        require(
            kind in ("added", "removed", "changed") and 0 <= cursor and 1 <= limit <= 200,
            "INVALID_PAGINATION",
            status=422,
        )
        c = store.get("comparison", id)
        require(c["status"] == "SUCCEEDED", "COMPARISON_NOT_READY")
        for key in ("left_run_id", "right_run_id"):
            with query.connect(run(c[key])):
                pass
        path = store.root / "comparisons" / (id + ".sqlite3")
        require(path.is_file() and not path.is_symlink(), "INDEX_UNAVAILABLE")
        st = path.stat()
        require(
            [st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns] == c["index_fingerprint"],
            "INDEX_UNAVAILABLE",
        )
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as cx:
            total = cx.execute("SELECT count(*) FROM changes WHERE kind=?", (kind,)).fetchone()[0]
            items = [
                json.loads(r[0])
                for r in cx.execute(
                    "SELECT payload FROM changes WHERE kind=? ORDER BY seq LIMIT ? OFFSET ?",
                    (kind, limit, cursor),
                )
            ]
        return envelope(
            {
                "items": items,
                "total": total,
                "next_cursor": cursor + limit if cursor + limit < total else None,
            }
        )

    return api
