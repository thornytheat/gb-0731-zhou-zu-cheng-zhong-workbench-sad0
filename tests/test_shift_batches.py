import csv
import hashlib
from io import BytesIO, StringIO
from zipfile import ZipFile


def run_shift(client):
    return client.post("/api/batches", json={"sample": "shift"})


def test_shift_batch_preview_archive_rerun_and_export(client):
    preview = client.get("/api/workbench?sample=shift").json()
    assert preview["manifest"] is None
    assert preview["data"]["settlement"]["ledger"]["balanced"] is True

    response = run_shift(client)
    assert response.status_code == 201
    manifest = response.json()["manifest"]
    run_id = manifest["run_id"]
    assert manifest["sample"] == "shift"
    assert manifest["ledger"]["settled"] == 4 and manifest["ledger"]["held"] == 3
    assert manifest["files"]["weighings"]["sha256"] == hashlib.sha256(
        (client.app.state.settings.source_dir / "shift-weighings.csv").read_bytes(),
    ).hexdigest()

    # 重跑同一批不新增趟次、不改写归档。
    rerun = client.post(f"/api/batches/{run_id}/rerun")
    assert rerun.status_code == 200
    assert rerun.json()["manifest"] == manifest
    assert client.get("/api/batches").json()["batches"] == [manifest]

    export = client.get(f"/api/batches/{run_id}/export")
    assert export.headers["Content-Disposition"] == f'attachment; filename="shift-{run_id[:12]}.zip"'
    with ZipFile(BytesIO(export.content)) as bundle:
        assert set(bundle.namelist()) == {"班结清单.json", "趟次结算.csv", "过磅归属.csv", "无主过磅.csv"}
        trips = list(csv.DictReader(StringIO(bundle.read("趟次结算.csv").decode("utf-8-sig"))))
        assert len(trips) == 7
        settled = [row for row in trips if row["状态"] == "结算"]
        assert len(settled) == 4
        weighings = list(csv.DictReader(StringIO(bundle.read("过磅归属.csv").decode("utf-8-sig"))))
        chosen = [row for row in weighings if row["计费选中"] == "是"]
        # 每个结算趟次恰好一条毛重、一条皮重被选中。
        assert len(chosen) == 8
        late = next(row for row in weighings if row["过磅号"] == "SW-018")
        assert late["晚到标记"] == "晚到" and late["计费选中"] == "否"


def test_shift_post_is_idempotent_and_rerun_adds_only_event(client):
    first = run_shift(client).json()["manifest"]
    second = run_shift(client)
    assert second.status_code == 200 and second.json() == {"manifest": first, "reused": True}
    cursor = client.get("/api/batches/events").json()["cursor"]
    client.post(f"/api/batches/{first['run_id']}/rerun")
    events = client.get(f"/api/batches/events?after={cursor}").json()["events"]
    assert [(event["seq"], event["code"]) for event in events] == [(cursor + 1, "REPLAYED")]
    assert client.get("/api/batches").json()["batches"] == [first]


def test_changed_shift_source_creates_new_run_without_rewriting(client, settings):
    original = run_shift(client).json()["manifest"]
    path = settings.source_dir / "shift-weighings.csv"
    path.write_bytes(path.read_bytes() + b"\r\n")
    reply = client.post(f"/api/batches/{original['run_id']}/rerun")
    assert (reply.status_code, reply.json()["error"]["code"]) == (409, "SOURCE_CHANGED")
    assert client.get("/api/batches").json()["batches"] == [original]
    new = run_shift(client).json()["manifest"]
    assert new["run_id"] != original["run_id"]
    assert new["ledger"] == original["ledger"]


def test_settlement_endpoint_and_live_export(client):
    api = client.get("/api/settlement?sample=shift")
    assert api.status_code == 200
    assert len(api.json()["data"]["rows"]) == 7
    live = client.get("/api/batches/export/shift")
    assert live.status_code == 200
    assert live.headers["Content-Disposition"] == 'attachment; filename="shift-preview.zip"'
    with ZipFile(BytesIO(live.content)) as bundle:
        assert "班结清单.json" in bundle.namelist()


def test_archived_shift_survives_restart_without_sources(client, settings):
    manifest = run_shift(client).json()["manifest"]
    run_id = manifest["run_id"]
    export = client.get(f"/api/batches/{run_id}/export").content
    for name in ("shift-triggers.csv", "shift-weighings.csv", "shift-passes.csv", "shift-certificates.csv"):
        (settings.source_dir / name).unlink()
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/batches/{run_id}").json()["manifest"] == manifest
        assert restarted.get(f"/api/batches/{run_id}/export").content == export
        assert restarted.get(f"/api/settlement?run_id={run_id}").json()["data"]["ledger"] == manifest["ledger"]
