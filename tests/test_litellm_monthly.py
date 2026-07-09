"""Tests for GET /api/litellm/monthly — math, clamping, zero-fill, no network."""
import tempfile
import unittest
from datetime import date

from token_dashboard.db import init_db, connect


def _seed(db_path, daily=(), models=()):
    with connect(db_path) as c:
        for r in daily:
            c.execute(
                "INSERT OR REPLACE INTO litellm_daily "
                "(date, spend, prompt_tokens, completion_tokens, total_tokens, api_requests, fetched_at) "
                "VALUES (?, ?, 0, 0, ?, ?, 'now')",
                (r["date"], r["spend"], r.get("total_tokens", 0), r.get("api_requests", 0)),
            )
        for r in models:
            c.execute(
                "INSERT OR REPLACE INTO litellm_daily_models "
                "(date, model, spend, prompt_tokens, completion_tokens, total_tokens, api_requests) "
                "VALUES (?, ?, ?, 0, 0, 0, 0)",
                (r["date"], r["model"], r["spend"]),
            )
        c.commit()


def _monthly(db_path, today):
    from token_dashboard.server import _ll_monthly
    return _ll_monthly(db_path, _today=today)


class TestSamePeriodClamping(unittest.TestCase):
    """day-of-month clamping when prev month is shorter."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_jul31_clamps_to_jun30(self):
        # today = Jul 31; prev = Jun which has 30 days → same_day = 30
        today = date(2025, 7, 31)
        result = _monthly(self.tmp.name, today)
        self.assertEqual(result["compare"]["same_day"], 30)
        self.assertEqual(result["compare"]["previous_month"], "2025-06")

    def test_mar30_clamps_to_feb28_non_leap(self):
        # 2025 is not a leap year → Feb has 28 days
        today = date(2025, 3, 30)
        result = _monthly(self.tmp.name, today)
        self.assertEqual(result["compare"]["same_day"], 28)
        self.assertEqual(result["compare"]["previous_month"], "2025-02")

    def test_mar30_clamps_to_feb29_leap(self):
        # 2024 is a leap year → Feb has 29 days
        today = date(2024, 3, 30)
        result = _monthly(self.tmp.name, today)
        self.assertEqual(result["compare"]["same_day"], 29)
        self.assertEqual(result["compare"]["previous_month"], "2024-02")

    def test_exact_match_no_clamp(self):
        # today = Jun 15; prev = May which has 31 days → same_day = 15
        today = date(2025, 6, 15)
        result = _monthly(self.tmp.name, today)
        self.assertEqual(result["compare"]["same_day"], 15)


class TestZeroFill(unittest.TestCase):
    """Empty months must appear as zero-spend entries."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_six_months_always_returned(self):
        today = date(2025, 7, 10)
        result = _monthly(self.tmp.name, today)
        self.assertEqual(len(result["months"]), 6)

    def test_empty_db_all_zeros(self):
        today = date(2025, 7, 10)
        result = _monthly(self.tmp.name, today)
        for m in result["months"]:
            self.assertEqual(m["spend"], 0.0)
            self.assertEqual(m["total_tokens"], 0)
            self.assertEqual(m["api_requests"], 0)

    def test_only_current_month_has_data(self):
        today = date(2025, 7, 10)
        _seed(self.tmp.name, daily=[{"date": "2025-07-05", "spend": 2.5}])
        result = _monthly(self.tmp.name, today)
        cur = next(m for m in result["months"] if m["month"] == "2025-07")
        self.assertEqual(cur["spend"], 2.5)
        for m in result["months"]:
            if m["month"] != "2025-07":
                self.assertEqual(m["spend"], 0.0)

    def test_month_ordering_oldest_first(self):
        today = date(2025, 7, 10)
        result = _monthly(self.tmp.name, today)
        months = [m["month"] for m in result["months"]]
        self.assertEqual(months, sorted(months))
        self.assertEqual(months[-1], "2025-07")


class TestProjection(unittest.TestCase):
    """projected_month_end = mtd / elapsed_days * days_in_month."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_projection_math(self):
        # Jul has 31 days; today is Jul 10 (elapsed=10); spend=$10 → projection=$31
        today = date(2025, 7, 10)
        _seed(self.tmp.name, daily=[
            {"date": "2025-07-01", "spend": 1.0},
            {"date": "2025-07-05", "spend": 4.0},
            {"date": "2025-07-10", "spend": 5.0},
        ])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["projected_month_end"], 31.0, places=3)
        self.assertEqual(result["compare"]["days_in_month"], 31)
        self.assertEqual(result["compare"]["elapsed_days"], 10)

    def test_projection_feb_non_leap(self):
        # Feb 2025 has 28 days; today is Feb 7 (elapsed=7); spend=$7 → projection=$28
        today = date(2025, 2, 7)
        _seed(self.tmp.name, daily=[{"date": "2025-02-07", "spend": 7.0}])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["projected_month_end"], 28.0, places=3)
        self.assertEqual(result["compare"]["days_in_month"], 28)

    def test_projection_feb_leap(self):
        # Feb 2024 has 29 days; today is Feb 7 (elapsed=7); spend=$7 → projection=$29
        today = date(2024, 2, 7)
        _seed(self.tmp.name, daily=[{"date": "2024-02-07", "spend": 7.0}])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["projected_month_end"], 29.0, places=3)
        self.assertEqual(result["compare"]["days_in_month"], 29)


class TestChangePct(unittest.TestCase):
    """change_pct is null when prev_same_period is 0."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_change_pct_null_when_prev_zero(self):
        today = date(2025, 7, 10)
        _seed(self.tmp.name, daily=[{"date": "2025-07-05", "spend": 5.0}])
        result = _monthly(self.tmp.name, today)
        self.assertIsNone(result["compare"]["change_pct"])

    def test_change_pct_positive(self):
        today = date(2025, 7, 10)
        _seed(self.tmp.name, daily=[
            {"date": "2025-06-10", "spend": 4.0},  # prev same period
            {"date": "2025-07-10", "spend": 6.0},  # current MTD
        ])
        result = _monthly(self.tmp.name, today)
        # (6-4)/4 * 100 = 50%
        self.assertAlmostEqual(result["compare"]["change_pct"], 50.0, places=1)

    def test_change_pct_negative(self):
        today = date(2025, 7, 10)
        _seed(self.tmp.name, daily=[
            {"date": "2025-06-10", "spend": 10.0},
            {"date": "2025-07-10", "spend": 5.0},
        ])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["change_pct"], -50.0, places=1)


class TestCurrentMtd(unittest.TestCase):
    """MTD only sums rows in the current calendar month."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_only_current_month_rows_included(self):
        today = date(2025, 7, 15)
        _seed(self.tmp.name, daily=[
            {"date": "2025-06-30", "spend": 99.0},  # yesterday's month — excluded
            {"date": "2025-07-01", "spend": 3.0},
            {"date": "2025-07-15", "spend": 2.0},
        ])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["current_mtd"], 5.0, places=4)

    def test_prev_same_period_stops_at_same_day(self):
        # same_day = 15; rows on Jun 16+ must be excluded from prev_same
        today = date(2025, 7, 15)
        _seed(self.tmp.name, daily=[
            {"date": "2025-06-15", "spend": 10.0},
            {"date": "2025-06-16", "spend": 500.0},  # must NOT be in same-period
        ])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["previous_same_period"], 10.0, places=4)

    def test_previous_month_total_includes_full_month(self):
        today = date(2025, 7, 15)
        _seed(self.tmp.name, daily=[
            {"date": "2025-06-01", "spend": 1.0},
            {"date": "2025-06-15", "spend": 2.0},
            {"date": "2025-06-30", "spend": 7.0},
        ])
        result = _monthly(self.tmp.name, today)
        self.assertAlmostEqual(result["compare"]["previous_month_total"], 10.0, places=4)


class TestModelDeltas(unittest.TestCase):
    """model_deltas: sorted by abs(delta) desc, top 8."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_delta_calculation(self):
        today = date(2025, 7, 10)
        _seed(self.tmp.name, models=[
            {"date": "2025-06-10", "model": "sonnet", "spend": 4.0},
            {"date": "2025-07-10", "model": "sonnet", "spend": 6.0},
        ])
        result = _monthly(self.tmp.name, today)
        m = next(d for d in result["model_deltas"] if d["model"] == "sonnet")
        self.assertAlmostEqual(m["delta"], 2.0, places=5)
        self.assertAlmostEqual(m["current_mtd_spend"], 6.0, places=5)
        self.assertAlmostEqual(m["previous_same_period_spend"], 4.0, places=5)

    def test_sorted_by_abs_delta(self):
        today = date(2025, 7, 10)
        _seed(self.tmp.name, models=[
            {"date": "2025-07-10", "model": "big",   "spend": 50.0},
            {"date": "2025-07-10", "model": "small", "spend": 1.0},
        ])
        result = _monthly(self.tmp.name, today)
        if len(result["model_deltas"]) >= 2:
            d0 = abs(result["model_deltas"][0]["delta"])
            d1 = abs(result["model_deltas"][1]["delta"])
            self.assertGreaterEqual(d0, d1)

    def test_at_most_8_models(self):
        today = date(2025, 7, 10)
        models = [{"date": "2025-07-10", "model": f"m{i}", "spend": float(i)} for i in range(12)]
        _seed(self.tmp.name, models=models)
        result = _monthly(self.tmp.name, today)
        self.assertLessEqual(len(result["model_deltas"]), 8)

    def test_reads_only_from_sqlite_no_network(self):
        # _ll_monthly must not make any HTTP calls — validated by running it
        # with no litellm config present; if it calls urlopen it would raise
        today = date(2025, 7, 10)
        result = _monthly(self.tmp.name, today)  # must not raise
        self.assertIn("months", result)
        self.assertIn("compare", result)
        self.assertIn("model_deltas", result)


class TestResponseShape(unittest.TestCase):
    """Output keys are always present."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def test_top_level_keys(self):
        result = _monthly(self.tmp.name, date(2025, 7, 10))
        self.assertIn("months", result)
        self.assertIn("compare", result)
        self.assertIn("model_deltas", result)

    def test_month_entry_keys(self):
        result = _monthly(self.tmp.name, date(2025, 7, 10))
        for m in result["months"]:
            for key in ("month", "spend", "total_tokens", "api_requests"):
                self.assertIn(key, m)

    def test_compare_keys(self):
        result = _monthly(self.tmp.name, date(2025, 7, 10))
        c = result["compare"]
        for key in ("current_mtd", "previous_same_period", "change_pct",
                    "projected_month_end", "previous_month_total",
                    "days_in_month", "elapsed_days", "same_day"):
            self.assertIn(key, c)

    def test_year_boundary_prev_month(self):
        # January → previous month is December of previous year
        today = date(2025, 1, 15)
        result = _monthly(self.tmp.name, today)
        self.assertEqual(result["compare"]["previous_month"], "2024-12")
        self.assertEqual(result["compare"]["same_day"], 15)


class TestKeyDeltas(unittest.TestCase):
    """key_deltas: per-key MTD vs same-period, sorted by abs(delta) desc."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        init_db(self.tmp.name)

    def _seed_keys(self, rows):
        with connect(self.tmp.name) as c:
            for r in rows:
                c.execute(
                    "INSERT OR REPLACE INTO litellm_daily_keys "
                    "(date, key_hash, key_alias, spend, prompt_tokens, completion_tokens, total_tokens, api_requests) "
                    "VALUES (?, ?, ?, ?, 0, 0, 0, 0)",
                    (r["date"], r["key_hash"], r.get("key_alias"), r["spend"]),
                )
            c.commit()

    def test_empty_keys_returns_empty_list(self):
        result = _monthly(self.tmp.name, date(2025, 7, 10))
        self.assertEqual(result["key_deltas"], [])

    def test_key_delta_calculation(self):
        today = date(2025, 7, 10)
        self._seed_keys([
            {"date": "2025-06-10", "key_hash": "abc123", "key_alias": "my-key", "spend": 4.0},
            {"date": "2025-07-10", "key_hash": "abc123", "key_alias": "my-key", "spend": 7.0},
        ])
        result = _monthly(self.tmp.name, today)
        k = next(d for d in result["key_deltas"] if d["key_alias"] == "my-key")
        self.assertAlmostEqual(k["delta"], 3.0, places=5)
        self.assertAlmostEqual(k["current_mtd_spend"], 7.0, places=5)
        self.assertAlmostEqual(k["previous_same_period_spend"], 4.0, places=5)

    def test_alias_fallback_to_truncated_hash(self):
        # No alias — falls back to first 12 chars of key_hash
        today = date(2025, 7, 10)
        self._seed_keys([
            {"date": "2025-07-10", "key_hash": "deadbeef1234abcd", "spend": 1.0},
        ])
        result = _monthly(self.tmp.name, today)
        self.assertEqual(len(result["key_deltas"]), 1)
        self.assertEqual(result["key_deltas"][0]["key_alias"], "deadbeef1234")

    def test_alias_preferred_over_hash(self):
        today = date(2025, 7, 10)
        self._seed_keys([
            {"date": "2025-07-10", "key_hash": "abcdef", "key_alias": "prod-key", "spend": 1.0},
        ])
        result = _monthly(self.tmp.name, today)
        self.assertEqual(result["key_deltas"][0]["key_alias"], "prod-key")

    def test_sorted_by_abs_delta_desc(self):
        today = date(2025, 7, 10)
        self._seed_keys([
            {"date": "2025-07-10", "key_hash": "small", "spend": 1.0},
            {"date": "2025-07-10", "key_hash": "large", "spend": 100.0},
        ])
        result = _monthly(self.tmp.name, today)
        deltas = [abs(k["delta"]) for k in result["key_deltas"]]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_same_period_clamping_applied(self):
        # today = Jul 31; same_day for Jun = 30; row on Jun 30 must be included
        today = date(2025, 7, 31)
        self._seed_keys([
            {"date": "2025-06-30", "key_hash": "kk1", "spend": 5.0},  # included
        ])
        result = _monthly(self.tmp.name, today)
        k = result["key_deltas"][0]
        self.assertAlmostEqual(k["previous_same_period_spend"], 5.0, places=5)

    def test_key_deltas_in_response_shape(self):
        result = _monthly(self.tmp.name, date(2025, 7, 10))
        self.assertIn("key_deltas", result)
        self.assertIsInstance(result["key_deltas"], list)


if __name__ == "__main__":
    unittest.main()
