from datetime import datetime, timezone

import pytest

from devradar.db import connect, init_schema
from devradar.usage import is_peak, month_cost, record_usage

UTC = timezone.utc


def test_peak_detection():
    assert is_peak(datetime(2026, 11, 23, 2, 30, tzinfo=UTC))      # Monday
    assert is_peak(datetime(2026, 11, 23, 7, 0, tzinfo=UTC))
    assert not is_peak(datetime(2026, 11, 23, 5, 0, tzinfo=UTC))
    assert not is_peak(datetime(2026, 11, 23, 10, 0, tzinfo=UTC))  # end is exclusive
    assert not is_peak(datetime(2026, 11, 28, 2, 30, tzinfo=UTC))  # Saturday


def test_cost_math(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    off = record_usage(conn, 1_000_000, 1_000_000, 1_000_000, datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    peak = record_usage(conn, 1_000_000, 1_000_000, 1_000_000, datetime(2026, 11, 23, 2, 0, tzinfo=UTC))
    assert off == pytest.approx(0.003 + 0.15 + 0.6)
    assert peak == pytest.approx(0.006 + 0.3 + 1.2)
    assert month_cost(conn, datetime(2026, 11, 25, tzinfo=UTC)) == pytest.approx(off + peak)
    assert month_cost(conn, datetime(2026, 12, 2, tzinfo=UTC)) == 0


def test_month_boundary_uses_bangkok_time(tmp_path):
    conn = connect(tmp_path / "d.db")
    init_schema(conn)
    # 2026-11-30 18:00 UTC is already 2026-12-01 01:00 in Bangkok
    record_usage(conn, 0, 1_000_000, 0, datetime(2026, 11, 30, 18, 0, tzinfo=UTC))
    assert month_cost(conn, datetime(2026, 12, 5, tzinfo=UTC)) == pytest.approx(0.15)
