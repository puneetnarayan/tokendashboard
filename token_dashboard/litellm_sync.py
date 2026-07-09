"""LiteLLM sync lifecycle — backfill, periodic refresh, failure handling."""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .litellm_config import clear_cache, discover, mask_key, validate
from .litellm_client import get_daily_activity, get_key_info, get_user_info
from .db import connect

log = logging.getLogger(__name__)

_SYNC_INTERVAL = 300        # 5 minutes
_BUDGET_REFRESH_CYCLES = 12 # refresh identity/budget every ~1h
_BACKFILL_DAYS = 90


class LiteLLMSync:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._cycle_count = 0
        self._last_state: Optional[str] = None  # tracks state transitions for log throttle

    # ── public ────────────────────────────────────────────────────────────

    def run_forever(self, interval: float = _SYNC_INTERVAL) -> None:
        self._startup_backfill()
        while True:
            self._wake.wait(timeout=interval)
            self._wake.clear()
            self._sync_cycle()

    def trigger_now(self) -> None:
        """Wake the sync loop immediately (called by POST /api/litellm/sync)."""
        self._wake.set()

    # ── lifecycle ─────────────────────────────────────────────────────────

    def _startup_backfill(self) -> None:
        """Best-effort backfill on startup. Failure is silent — next cycle retries."""
        try:
            self._do_backfill()
        except Exception as e:
            log.debug("litellm startup backfill deferred: %s", e)

    def _sync_cycle(self) -> None:
        with self._lock:
            self._cycle_count += 1
            clear_cache()
            config = discover()
            if config is None:
                self._set_state("not_configured")
                return

            state, user_info = validate(config)
            if state == "invalid_key":
                self._transition_state("invalid_key")
                return
            if state == "unreachable":
                self._transition_state("unreachable")
                return

            # active — clear any prior error state
            if self._last_state and self._last_state != "active":
                log.info("litellm: proxy reachable again, resuming sync")
            self._last_state = "active"

            try:
                self._do_backfill_or_refresh(config, user_info)
                if self._cycle_count % _BUDGET_REFRESH_CYCLES == 0:
                    self._refresh_identity(config, user_info)
            except Exception as e:
                log.warning("litellm sync cycle error: %s", e)
                self._set_state("unreachable")

    def _do_backfill(self) -> None:
        """Called at startup — may throw; caller catches."""
        config = discover()
        if config is None:
            return
        state, user_info = validate(config)
        if state != "active":
            return
        self._do_backfill_or_refresh(config, user_info)

    def _do_backfill_or_refresh(self, config: dict, user_info: Optional[dict]) -> None:
        """Compute the gap in litellm_daily and fetch it.

        Self-healing: always fetches from min(existing date) to today so
        gaps from failed startup or VPN-off periods are filled automatically.
        """
        today = date.today()
        start = self._gap_start(today)
        end = today

        rows = get_daily_activity(
            config["base_url"], config["api_key"],
            start.isoformat(), end.isoformat(),
        )
        self._write_rows(rows, today)
        self._set_meta("last_sync", _utcnow())
        self._set_meta("proxy_origin", config["base_url"])
        self._set_state("active")

        if user_info:
            self._write_user_info(user_info)

        self._refresh_budget(config)

    def _refresh_identity(self, config: dict, user_info: Optional[dict]) -> None:
        fresh = get_user_info(config["base_url"], config["api_key"])
        if fresh:
            self._write_user_info(fresh)
        self._refresh_budget(config)

    def _refresh_budget(self, config: dict) -> None:
        ki = get_key_info(config["base_url"], config["api_key"])
        if not ki:
            return
        for field, meta_key in (
            ("max_budget",      "budget_max"),
            ("spend",           "budget_spend"),
            ("budget_duration", "budget_duration"),
        ):
            val = ki.get(field)
            if val is not None:
                self._set_meta(meta_key, str(val))

    # ── DB helpers ────────────────────────────────────────────────────────

    def _gap_start(self, today: date) -> date:
        """Return earliest date to fetch: min(existing) or today-90d."""
        floor = today - timedelta(days=_BACKFILL_DAYS)
        try:
            with connect(self._db_path) as c:
                row = c.execute("SELECT MIN(date) FROM litellm_daily").fetchone()
                if row and row[0]:
                    existing = date.fromisoformat(row[0])
                    return min(existing, floor)
        except Exception:
            pass
        return floor

    def _write_rows(self, rows: list[dict], today: date) -> None:
        yesterday = today - timedelta(days=1)
        with connect(self._db_path) as c:
            for row in rows:
                d = row["date"]
                is_recent = d >= yesterday.isoformat()
                policy = "INSERT OR REPLACE" if is_recent else "INSERT OR IGNORE"
                fetched_at = _utcnow()
                c.execute(
                    f"{policy} INTO litellm_daily "
                    "(date, spend, prompt_tokens, completion_tokens, total_tokens, api_requests, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (d, row["spend"], row["prompt_tokens"], row["completion_tokens"],
                     row["total_tokens"], row["api_requests"], fetched_at),
                )
                for model, mv in (row.get("models") or {}).items():
                    c.execute(
                        f"{policy} INTO litellm_daily_models "
                        "(date, model, spend, prompt_tokens, completion_tokens, total_tokens, api_requests) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (d, model, mv["spend"], mv["prompt_tokens"],
                         mv["completion_tokens"], mv["total_tokens"], mv["api_requests"]),
                    )
                for key_hash, kv in (row.get("keys") or {}).items():
                    c.execute(
                        f"{policy} INTO litellm_daily_keys "
                        "(date, key_hash, key_alias, spend, prompt_tokens, completion_tokens, total_tokens, api_requests) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (d, key_hash, kv.get("alias"),
                         kv["spend"], kv["prompt_tokens"],
                         kv["completion_tokens"], kv["total_tokens"], kv["api_requests"]),
                    )
            c.commit()

    def _write_user_info(self, info: dict) -> None:
        for field, key in (
            ("user_id",    "user_id"),
            ("user_alias", "user_alias"),
            ("max_budget", "budget_max"),
            ("spend",      "budget_spend"),
            ("budget_duration", "budget_duration"),
        ):
            val = info.get(field)
            if val is not None:
                self._set_meta(key, str(val))

    def _set_state(self, state: str) -> None:
        self._set_meta("state", state)

    def _set_meta(self, key: str, value: str) -> None:
        try:
            with connect(self._db_path) as c:
                c.execute(
                    "INSERT OR REPLACE INTO litellm_meta (key, value) VALUES (?, ?)",
                    (key, value),
                )
                c.commit()
        except Exception as e:
            log.debug("litellm meta write failed (%s): %s", key, e)

    def _transition_state(self, new_state: str) -> None:
        """Set state and log the transition once; subsequent same-state = debug."""
        if self._last_state != new_state:
            log.info("litellm: state → %s", new_state)
            self._last_state = new_state
        else:
            log.debug("litellm: still %s", new_state)
        self._set_state(new_state)


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
