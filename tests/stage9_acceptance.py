"""Offline, real SQLite/storage/API persistence across two fresh application processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from tests.browser_ui_smoke import ROOT, configure, filesystem, fake_boundaries


def phase(runtime, operation):
    runtime = runtime.resolve()
    assert runtime.is_relative_to((ROOT / ".pytest-runtime").resolve())
    assert runtime.name.startswith("stage9-restart-")
    configure(runtime)
    # No provider/network traffic can escape the deterministic boundaries.
    import socket
    import httpx
    original_connect = socket.socket.connect
    def denied(*args, **kwargs):
        raise AssertionError("External network forbidden in restart acceptance")
    def guarded_connect(sock, address):
        if sys._getframe(1).f_code is getattr(socket.socketpair, "__code__", None):
            return original_connect(sock, address)
        return denied()
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = denied
    socket.getaddrinfo = denied
    httpx.HTTPTransport.handle_request = denied
    httpx.AsyncHTTPTransport.handle_async_request = denied
    png, captures = fake_boundaries()
    from backend.main import app
    from backend.database import SessionLocal
    from backend.models.db import Competitor, Source, SourceSnapshot, Analysis, Comparison
    from backend.models.analysis import CompetitorAnalysis, ComparisonResult
    from fastapi.testclient import TestClient
    import pymupdf
    manifest = runtime / "acceptance-identities.json"
    with TestClient(app) as client:
        if operation == "create":
            ids = [client.post("/api/v2/competitors", json={"name": name}).json()["id"] for name in ("Alpha", "Beta", "Gamma")]
            roots = [f"/api/v2/competitors/{cid}/sources" for cid in ids]
            responses = [client.post(root + "/text", json={"text": "Automation for teams", "label": "Offer"}) for root in roots]
            responses.append(client.post(roots[0] + "/file", files={"file": ("original.png", png, "image/png")}))
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                page.insert_text((30, 40), "PDF visible offer for teams")
                content = pdf.tobytes()
            responses.append(client.post(roots[0] + "/file", files={"file": ("original.pdf", content, "application/pdf")}))
            responses.append(client.post(roots[0] + "/url", json={"url": "https://public.example/"}))
            assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
            url_source = responses[-1].json()["source"]["id"]
            before = client.get(f"/api/v2/sources/{url_source}").json()
            refresh = client.post(f"/api/v2/sources/{url_source}/refresh")
            assert refresh.status_code == 201
            refreshed = refresh.json()
            assert len(refreshed["snapshots"]) == 2
            assert refreshed["snapshots"][-1]["id"] != before["snapshots"][-1]["id"]
            capture_count = len(captures)
            again = client.post(f"/api/v2/sources/{url_source}/reanalyze")
            assert again.status_code == 201 and len(captures) == capture_count
            assert len(again.json()["analyses"]) == 3 and len(again.json()["snapshots"]) == 2
            aggregate_ids = []
            for cid in ids:
                result = client.post(f"/api/v2/competitors/{cid}/aggregate-analysis")
                assert result.status_code == 201, result.text
                data = result.json()
                assert data["analysis_type"] == "aggregate" and data["snapshot_id"] is None
                CompetitorAnalysis.model_validate(data["result_json"])
                aggregate_ids.append(data["id"])
            comparison = client.post("/api/v2/comparisons", json={"competitor_ids": ids[:2]})
            assert comparison.status_code == 201, comparison.text
            ComparisonResult.model_validate(comparison.json())
            with SessionLocal() as db:
                row = db.query(Comparison).one()
                expected = {
                    "competitors": ids, "sources": sorted(s.id for s in db.query(Source)),
                    "snapshots": sorted(s.id for s in db.query(SourceSnapshot)),
                    "analyses": sorted(s.id for s in db.query(Analysis)), "aggregate_ids": aggregate_ids,
                    "comparison_id": row.id, "comparison_result": json.loads(row.result_json),
                    "counts": [db.query(m).count() for m in (Competitor, Source, SourceSnapshot, Analysis, Comparison)],
                    "artifacts": {str(p.relative_to(runtime)): p.read_bytes().hex() for folder in ("uploads", "screenshots") for p in (runtime / folder).iterdir()},
                }
            manifest.write_text(json.dumps(expected), encoding="utf-8")
            print("BEFORE_RESTART: " + json.dumps(expected["counts"]) + "; source types=text,image,pdf,url; refresh new snapshot; reanalyze no capture; 3 aggregates; compare 2 PASS")
        else:
            expected = json.loads(manifest.read_text(encoding="utf-8"))
            assert {c["id"] for c in client.get("/api/v2/competitors").json()} == set(expected["competitors"])
            for sid in expected["sources"]:
                response = client.get(f"/api/v2/sources/{sid}")
                assert response.status_code == 200
                assert response.json()["analyses"]
            for aid in expected["analyses"]:
                response = client.get(f"/api/v2/analyses/{aid}")
                assert response.status_code == 200
                CompetitorAnalysis.model_validate(response.json()["result_json"])
            with SessionLocal() as db:
                assert [db.query(m).count() for m in (Competitor, Source, SourceSnapshot, Analysis, Comparison)] == expected["counts"]
                assert sorted(s.id for s in db.query(SourceSnapshot)) == expected["snapshots"]
                assert {s.source_type.value for s in db.query(Source)} == {"text", "image", "pdf", "url"}
                for aid in expected["aggregate_ids"]:
                    row = db.get(Analysis, aid)
                    assert row.analysis_type == "aggregate" and row.snapshot_id is None
                from backend.repositories.comparisons import get_comparison
                row = get_comparison(db, expected["comparison_id"])
                assert json.loads(row.result_json) == expected["comparison_result"]
                assert json.loads(row.competitor_ids_json) == expected["competitors"][:2]
            actual = {str(p.relative_to(runtime)): p.read_bytes().hex() for folder in ("uploads", "screenshots") for p in (runtime / folder).iterdir()}
            assert actual == expected["artifacts"]
            assert client.get("/api/v2/health").json()["database"] == "ready"
            print("AFTER_RESTART: identities/counts/artifact bytes unchanged; aggregates + repository comparison persistence PASS")
    assert not app.state.started and not app.state.database_ready
    print("APPLICATION_LIFESPAN_STOPPED_CLEANLY")


def main():
    before = filesystem()
    parent = ROOT / ".pytest-runtime"
    parent.mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix="stage9-restart-", dir=parent) as directory:
            for operation in ("create", "verify"):
                result = subprocess.run([sys.executable, "-m", "tests.stage9_acceptance", operation, directory],
                                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=90)
                print(result.stdout)
                assert result.returncode == 0, result.stderr
    finally:
        assert filesystem() == before, "Production filesystem changed"
        if not any(parent.iterdir()):
            parent.rmdir()
    print("FRESH_PROCESS_RESTART_ACCEPTANCE_PASS; temporary runtime cleaned; production unchanged; external network=0")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        phase(Path(sys.argv[2]), sys.argv[1])
    else:
        main()
