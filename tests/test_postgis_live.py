"""Live PostGIS write-back integration (opt-in via GEOQA_PG_URL)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

pytest.importorskip("geopandas")
pytest.importorskip("sqlalchemy")

from geoqa.config import load_suite  # noqa: E402
from geoqa.engine import run_suite  # noqa: E402
from geoqa.result import Status  # noqa: E402

pytestmark = pytest.mark.postgis


def _pg_url() -> str:
    url = os.environ.get("GEOQA_PG_URL", "").strip().rstrip("/")
    if not url:
        pytest.skip("GEOQA_PG_URL not set")
    return f"{url}/geoqa_test"


@pytest.fixture
def live_repair_table():
    """Create a throwaway table with a bowtie; drop on teardown."""
    from sqlalchemy import create_engine, text

    conn = _pg_url()
    table = f"geoqa_repair_live_{uuid.uuid4().hex[:12]}"
    fq = f"public.{table}"
    eng = create_engine(conn)
    try:
        with eng.begin() as c:
            c.execute(
                text(
                    f"""
                    CREATE TABLE {fq} (
                      id integer PRIMARY KEY,
                      name text,
                      geom geometry(Geometry, 4326)
                    )
                    """
                )
            )
            c.execute(
                text(
                    f"""
                    INSERT INTO {fq} (id, name, geom) VALUES
                      (1, 'ok', ST_GeomFromText(
                        'POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))', 4326)),
                      (2, 'bowtie', ST_GeomFromText(
                        'POLYGON((0 0, 2 2, 0 2, 2 0, 0 0))', 4326))
                    """
                )
            )
        yield conn, fq
    finally:
        try:
            with eng.begin() as c:
                c.execute(text(f"DROP TABLE IF EXISTS {fq}"))
        finally:
            eng.dispose()


def test_postgis_live_write_updates_invalid_geometry(tmp_path: Path, live_repair_table):
    conn, fq = live_repair_table
    cfg = tmp_path / "live.yml"
    cfg.write_text(
        f"""version: 1
name: live-write
sources:
  - connection: "{conn}"
    table: {fq}
    geom_column: geom
    name: repair_live
defaults:
  geometry:
    valid: true
    repair:
      enabled: true
      make_valid: true
      write_mode: postgis
      then_recheck: true
      postgis:
        connection: "{conn}"
        table: {fq}
        geom_column: geom
        id_column: id
        dry_run: false
""",
        encoding="utf-8",
    )

    report = run_suite(load_suite(cfg), allow_postgis_write=True)
    by_check = {r.check: r for r in report.all_results}

    assert by_check["geometry.valid"].status == Status.FAIL
    assert by_check["geometry.repair.postgis"].status == Status.PASS
    assert "updated" in by_check["geometry.repair.postgis"].message.lower()
    assert by_check["geometry.valid.after_repair"].status == Status.PASS

    from sqlalchemy import create_engine, text

    eng = create_engine(conn)
    with eng.connect() as c:
        invalid = c.execute(
            text(f"SELECT COUNT(*) FROM {fq} WHERE NOT ST_IsValid(geom)")
        ).scalar()
    eng.dispose()
    assert invalid == 0


def test_postgis_live_write_refused_without_allow_flag(tmp_path: Path, live_repair_table):
    conn, fq = live_repair_table
    cfg = tmp_path / "refuse.yml"
    cfg.write_text(
        f"""version: 1
name: refuse-live
sources:
  - connection: "{conn}"
    table: {fq}
    geom_column: geom
    name: repair_live
defaults:
  geometry:
    valid: true
    repair:
      enabled: true
      make_valid: true
      write_mode: postgis
      postgis:
        connection: "{conn}"
        table: {fq}
        geom_column: geom
        id_column: id
        dry_run: false
""",
        encoding="utf-8",
    )

    report = run_suite(load_suite(cfg), allow_postgis_write=False)
    by_check = {r.check: r for r in report.all_results}
    assert by_check["geometry.repair.postgis"].status == Status.PASS
    assert "dry_run" in by_check["geometry.repair.postgis"].message.lower()

    from sqlalchemy import create_engine, text

    eng = create_engine(conn)
    with eng.connect() as c:
        invalid = c.execute(
            text(f"SELECT COUNT(*) FROM {fq} WHERE NOT ST_IsValid(geom)")
        ).scalar()
    eng.dispose()
    assert invalid >= 1
