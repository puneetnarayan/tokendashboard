"""Proxy token cost per skill: invocations × catalog token estimate."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .db import skill_breakdown
from .skills import cached_catalog


def skill_cost_proxy(
    db_path: Union[str, Path],
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> list:
    rows = skill_breakdown(db_path, since=since, until=until)
    catalog = cached_catalog()
    out = []
    for r in rows:
        info = catalog.get(r["skill"])
        if info is None:
            out.append({
                **r,
                "proxy_tokens": None,
                "chars": None,
                "catalog_missing": True,
                "description": "",
                "file_path": None,
            })
        else:
            out.append({
                **r,
                "proxy_tokens": r["invocations"] * info["tokens"],
                "chars": info["chars"],
                "catalog_missing": False,
                "description": info.get("description", ""),
                "file_path": info.get("path"),
            })
    # Sort: known skills by proxy_tokens desc, unknown last (stable).
    out.sort(key=lambda x: (x["proxy_tokens"] is None, -(x["proxy_tokens"] or 0)))
    return out
