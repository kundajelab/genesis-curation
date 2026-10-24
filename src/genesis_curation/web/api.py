"""Authenticated browser boundary; no arbitrary file paths, shell or deployment tools."""

from __future__ import annotations

import csv
import hmac
import io
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from ..contracts.records import canonical, fingerprint, loads
from ..llm import providers
from . import catalog, inbox, runtime, skills, studies, study_api

LIMIT = 3 * 1024 * 1024


class Boundary(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, root: Path, origin: str, dev: bool) -> None:
        super().__init__(app)
        self.root, self.origin, self.dev = root, origin.rstrip("/"), dev

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in {"GET", "POST"}:
            return JSONResponse({"error": "Method not allowed"}, status_code=405)
        if request.method == "POST":
            if request.headers.get("origin") != self.origin:
                return JSONResponse({"error": "Invalid request origin"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "JSON required"}, status_code=415)
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > LIMIT:
                    return JSONResponse({"error": "Request exceeds upload budget"}, status_code=413)
            request._body = bytes(content)
            try:
                loads(request._body.decode("utf-8"))
            except ValueError, UnicodeDecodeError:
                return JSONResponse({"error": "Expected a valid JSON object"}, status_code=400)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/login":
            account = runtime.session(self.root, request.cookies.get("genesis_session", ""))
            if not account:
                return JSONResponse({"error": "Login required"}, status_code=401)
            request.state.account = account
            if request.method == "POST" and not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), account["csrf"]
            ):
                return JSONResponse({"error": "Invalid CSRF token"}, status_code=403)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'",
            }
        )
        if not self.dev:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response


def create_app(
    root: Path,
    config_path: Path,
    origin: str,
    *,
    dev: bool = False,
    allow_insecure_http: bool = False,
) -> FastAPI:
    if not dev and not allow_insecure_http and not origin.startswith("https://"):
        raise ValueError("Public application requires an HTTPS origin")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Boundary, root=root, origin=origin, dev=dev or allow_insecure_http)
    registry = root / "catalog"

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            {"error": str(error)}, status_code=409 if "Stale" in str(error) else 400
        )

    @app.exception_handler(KeyError)
    async def missing(_request: Request, _error: KeyError) -> JSONResponse:
        return JSONResponse({"error": "Missing required request field"}, status_code=400)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(request: Request) -> Response:
        body = await request.json()
        name, password = body.get("username", ""), body.get("password", "")
        if not isinstance(name, str) or not isinstance(password, str) or len(password) > 1024:
            raise ValueError("Invalid login")
        try:
            token, csrf = runtime.login(
                root, name, password, request.client.host if request.client else "unknown"
            )
        except ValueError as error:
            return JSONResponse(
                {"error": str(error)}, status_code=429 if "Too many" in str(error) else 401
            )
        response = JSONResponse({"actor": name, "csrf": csrf})
        response.set_cookie(
            "genesis_session",
            token,
            httponly=True,
            secure=not (dev or allow_insecure_http),
            samesite="strict",
            max_age=12 * 3600,
            path="/",
        )
        return response

    @app.get("/api/session")
    def session(request: Request) -> dict[str, str]:
        return {"actor": request.state.account["actor"], "csrf": request.state.account["csrf"]}

    @app.post("/api/logout")
    def logout(request: Request) -> Response:
        runtime.logout(root, request.cookies.get("genesis_session", ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie("genesis_session")
        return response

    @app.get("/api/providers")
    def configured_providers() -> list[dict[str, Any]]:
        if not config_path.exists():
            return []
        return [
            {
                k: p.get(k)
                for k in ("id", "model", "mode", "external", "protocol", "allowed_classifications")
            }
            for p in providers.configurations(config_path)
        ]

    @app.get("/api/skills")
    def available_skills() -> list[dict[str, Any]]:
        return skills.available(root / "skills")

    @app.get("/api/subjects")
    def subjects() -> list[dict[str, Any]]:
        return catalog.listing(registry)

    @app.get("/api/inbox")
    def review_inbox() -> dict[str, Any]:
        return inbox.listing(registry)

    @app.post("/api/subjects/{subject}/resolve")
    async def resolve_task(subject: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return inbox.resolve(
            registry,
            subject,
            body["version"],
            body["fields"],
            body["action"],
            body["reason"],
            request.state.account["actor"],
        )

    @app.post("/api/prepare")
    async def prepare_inbox(request: Request) -> dict[str, Any]:
        body = await request.json()
        ids = body.get("subjects")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 1000
            or any(not isinstance(key, str) for key in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Select 1–1000 unique records to prepare")
        config = providers.select(config_path, body["provider"])
        skill = skills.select(root / "skills", body["skill"])
        records, skipped = [], []
        for key in ids:
            state = catalog.get(registry, key)
            # Preparation never clears existing work. Explicit re-analysis remains separate.
            if state["proposal"] or state["reviews"]:
                skipped.append(key)
                continue
            if (
                state["classification"] not in config["allowed_classifications"]
                or state["classification"] == "local-only"
                and config.get("external")
            ):
                raise ValueError("Provider is not authorized for every selected record")
            records.append({"subject": key, "version": state["version"]})
        if not records:
            return {"id": None, "skipped": skipped}
        payload = {
            "records": records,
            "provider": body["provider"],
            "provider_hash": fingerprint(config),
            "skill": body["skill"],
            "skill_hash": skill["sha256"],
        }
        key = runtime.enqueue(root, "prepare", request.state.account["actor"], payload)
        return {"id": key, "skipped": skipped}

    @app.get("/api/subjects/{subject}")
    def subject_view(subject: str) -> dict[str, Any]:
        return catalog.get(registry, subject)

    @app.get("/api/subjects/{subject}/history")
    def history(subject: str) -> list[dict[str, Any]]:
        return catalog.history(registry, subject)

    @app.post("/api/imports/preview")
    async def preview(request: Request) -> dict[str, Any]:
        body = await request.json()
        rows = catalog.preview(body["text"], body["filename"])
        return {"rows": rows, "columns": list(dict.fromkeys(k for row in rows for k in row))}

    @app.post("/api/imports/commit")
    async def commit(request: Request) -> dict[str, Any]:
        body = await request.json()
        source = None
        if body.get("job"):
            saved = runtime.job(root, body["job"])
            if saved["kind"] != "accession" or saved["status"] != "SUCCEEDED":
                raise ValueError("Accession preview is not ready")
            body.update({k: saved["result"][k] for k in ("text", "filename", "id_column")})
            source = saved["result"]["source"]
        ids = catalog.import_rows(
            registry,
            body["text"],
            body["filename"],
            body["namespace"],
            body["id_column"],
            body["classification"],
            request.state.account["actor"],
        )
        if source:
            for subject in ids:
                current = catalog.get(registry, subject)
                # ENA row evidence is already scoped. Do not attach the entire multi-sample
                # response to every sample and accidentally mix their biological metadata.
                if source["media"] != "application/json":
                    catalog.attach(
                        registry,
                        subject,
                        current["version"],
                        source["text"],
                        source["url"],
                        "public",
                        request.state.account["actor"],
                    )
        studies.bootstrap(registry)
        return {"subjects": ids}

    @app.post("/api/subjects/{subject}/evidence")
    async def evidence(subject: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return catalog.attach(
            registry,
            subject,
            body["version"],
            body["text"],
            "curator:" + body["label"],
            body["classification"],
            request.state.account["actor"],
        )

    @app.post("/api/subjects/{subject}/decisions")
    async def decision(subject: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return catalog.decide(
            registry,
            subject,
            body["version"],
            body["field"],
            body["action"],
            body["reason"],
            request.state.account["actor"],
            body.get("correction"),
        )

    @app.post("/api/subjects/{subject}/examples")
    async def example(subject: str, request: Request) -> dict[str, Any]:
        body = await request.json()
        return catalog.promote(
            registry, subject, body["version"], body["field"], request.state.account["actor"]
        )

    @app.get("/api/jobs")
    def jobs() -> list[dict[str, Any]]:
        return runtime.jobs(root)

    @app.get("/api/jobs/{key}")
    def job(key: str) -> dict[str, Any]:
        return runtime.job(root, key)

    @app.post("/api/jobs")
    async def enqueue(request: Request) -> dict[str, str]:
        body = await request.json()
        kind, payload = body["kind"], body["payload"]
        required = {
            "propose": {"subject", "version", "provider", "skill"},
            "chat": {"subject", "version", "provider", "question"},
            "accession": {"accession"},
            "probe": {"provider"},
        }
        if (
            kind not in required
            or not isinstance(payload, dict)
            or not required[kind].issubset(payload)
        ):
            raise ValueError("Invalid job request")
        if any(not isinstance(v, str) or len(v) > 8000 for v in payload.values()):
            raise ValueError("Invalid job parameter")
        if "subject" in payload:
            state = catalog.get(registry, payload["subject"])
            catalog.expect(state, payload["version"])
            config = providers.select(config_path, payload["provider"])
            if state["classification"] not in config["allowed_classifications"]:
                raise ValueError("Provider is not authorized for this data classification")
        if "provider" in payload:
            payload["provider_hash"] = fingerprint(
                providers.select(config_path, payload["provider"])
            )
        if kind == "propose":
            payload["skill_hash"] = skills.select(root / "skills", payload["skill"])["sha256"]
        return {"id": runtime.enqueue(root, kind, request.state.account["actor"], payload)}

    @app.post("/api/jobs/{key}/cancel")
    def cancel(key: str) -> dict[str, bool]:
        job = runtime.job(root, key)
        runtime.cancel(root, key)
        if job["status"] == "QUEUED" and job["payload"].get("study"):
            studies.preparation(registry, job["payload"]["study"], {"status": "INTERRUPTED"})
        return {"ok": True}

    @app.get("/api/export")
    def export(format: str = "json") -> Response:
        result = catalog.export(registry)
        if format == "json":
            content, media = canonical(result), "application/json"
        elif format in {"csv", "tsv"}:
            columns = ["id", "name", "version", "review_status", "unresolved"] + sorted(
                harmonize_fields()
            )
            stream = io.StringIO()
            writer = csv.DictWriter(
                stream, fieldnames=columns, delimiter="\t" if format == "tsv" else ","
            )
            writer.writeheader()
            for row in result["records"]:
                values = {k: row[k] for k in columns[:5]} | row["metadata"]
                values["unresolved"] = ";".join(values["unresolved"])
                # Prevent spreadsheet formulas when opening exports.
                writer.writerow(
                    {
                        k: "'" + v
                        if isinstance(v, str) and v.startswith(("=", "+", "-", "@"))
                        else v
                        for k, v in values.items()
                    }
                )
            content, media = (
                stream.getvalue(),
                "text/csv" if format == "csv" else "text/tab-separated-values",
            )
        else:
            raise ValueError("Unknown export format")
        return Response(
            content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="curated-metadata.{format}"'},
        )

    study_api.mount(app, root, config_path)

    @app.get("/{path:path}")
    def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unknown API endpoint")
        assets = Path(__file__).parent / "assets"
        candidate = (assets / (path or "index.html")).resolve()
        if not candidate.is_relative_to(assets.resolve()) or not candidate.is_file():
            candidate = assets / "index.html"
        if not candidate.is_file():
            raise ValueError("Frontend not built; run the documented frontend build")
        return FileResponse(candidate)

    return app


def harmonize_fields() -> set[str]:
    from ..curation.harmonize import FIELDS

    return FIELDS
