"""Investments dashboard — stock watchlist performance API endpoints."""

import os
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from shared.db import get_session
from webhook.dashboard_shared import _parse_date_range, _response_metadata

router = APIRouter()

MAX_TICKERS_PER_QUERY = 50
INVESTMENTS_MAX_DAYS = 10 * 365


@router.get("/dashboard/investments")
def serve_investments():
    """Serve the investments dashboard HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "investments.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())


def _resolve_tickers(session, tickers_param: str | None, group_param: int | None) -> list[str]:
    """Resolve ticker list from either explicit tickers or group membership."""
    if tickers_param:
        tickers = [t.strip().upper() for t in tickers_param.split(",") if t.strip()]
        if len(tickers) > MAX_TICKERS_PER_QUERY:
            raise HTTPException(
                400,
                detail={
                    "code": 400,
                    "message": f"Too many tickers ({len(tickers)}), max {MAX_TICKERS_PER_QUERY}",
                },
            )
        return tickers

    if group_param is not None:
        rows = session.execute(
            text("""
                SELECT w.ticker
                FROM investments.stock_group_members sgm
                JOIN investments.watchlist w ON w.id = sgm.watchlist_id
                WHERE sgm.group_id = :gid AND w.active = true
            """),
            {"gid": group_param},
        ).fetchall()
        return [r.ticker for r in rows]

    # Default: all active tickers
    rows = session.execute(
        text("SELECT ticker FROM investments.watchlist WHERE active = true")
    ).fetchall()
    return [r.ticker for r in rows]


def _get_data_freshness(session) -> dict:
    """Return latest price date and collector watermark for trust/debuggability."""
    row = session.execute(text("""
        SELECT
            MAX(dp.date) as last_price_date,
            (SELECT MAX(finished_at) FROM basin.collector_runs
             WHERE collector = 'nasdaq' AND status = 'success') as last_collector_run
        FROM investments.daily_prices dp
    """)).fetchone()

    return {
        "last_price_date": row.last_price_date.isoformat() if row.last_price_date else None,
        "last_collector_run": row.last_collector_run.isoformat() if row.last_collector_run else None,
    }


@router.get("/api/investments/watchlist")
def get_watchlist(
    start: str | None = None,
    end: str | None = None,
    group: int | None = None,
):
    """Watchlist with performance metrics for the selected period."""
    start_date, end_date = _parse_date_range(start, end, max_days=INVESTMENTS_MAX_DAYS)

    with get_session() as session:
        # Period start/end prices via window functions
        rows = session.execute(
            text("""
                WITH period_bounds AS (
                    SELECT
                        w.id, w.ticker, w.name, w.is_benchmark, w.sector,
                        FIRST_VALUE(dp.close) OVER (
                            PARTITION BY w.id ORDER BY dp.date ASC
                        ) as start_price,
                        FIRST_VALUE(dp.close) OVER (
                            PARTITION BY w.id ORDER BY dp.date DESC
                        ) as end_price,
                        MIN(dp.date) OVER (PARTITION BY w.id) as first_date,
                        MAX(dp.date) OVER (PARTITION BY w.id) as last_date,
                        ROW_NUMBER() OVER (
                            PARTITION BY w.id ORDER BY dp.date DESC
                        ) as rn
                    FROM investments.watchlist w
                    JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
                    WHERE w.active = true
                      AND dp.date BETWEEN :start AND :end
                )
                SELECT ticker, name, start_price, end_price, first_date, last_date, is_benchmark, sector
                FROM period_bounds
                WHERE rn = 1
            """),
            {"start": start_date, "end": end_date},
        ).fetchall()

        # Latest close across all available data (not just the period)
        latest_rows = session.execute(text("""
            SELECT DISTINCT ON (w.ticker)
                w.ticker, dp.close as latest_close, dp.date as latest_close_date
            FROM investments.watchlist w
            JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
            WHERE w.active = true
            ORDER BY w.ticker, dp.date DESC
        """)).fetchall()
        latest_map = {r.ticker: (float(r.latest_close), r.latest_close_date.isoformat()) for r in latest_rows}

        # 52-week high/low (trailing, independent of selected period)
        hi_lo_rows = session.execute(text("""
            SELECT w.ticker, MAX(dp.high) as high_52w, MIN(dp.low) as low_52w
            FROM investments.watchlist w
            JOIN investments.daily_prices dp ON dp.watchlist_id = w.id
            WHERE w.active = true
              AND dp.date >= CURRENT_DATE - INTERVAL '52 weeks'
            GROUP BY w.ticker
        """)).fetchall()
        hi_lo_map = {
            r.ticker: (float(r.high_52w) if r.high_52w else None, float(r.low_52w) if r.low_52w else None)
            for r in hi_lo_rows
        }

        # Groups
        groups = _fetch_groups(session)

        # Data freshness
        freshness = _get_data_freshness(session)

    # Build response with computed metrics
    stocks = []
    for r in rows:
        start_price = float(r.start_price)
        end_price = float(r.end_price)
        days = (r.last_date - r.first_date).days
        years = days / 365.25

        period_return_pct = ((end_price - start_price) / start_price) * 100 if start_price else 0
        if years > 0 and start_price > 0:
            cagr_pct = ((end_price / start_price) ** (1 / years) - 1) * 100
        else:
            cagr_pct = 0

        hi_lo = hi_lo_map.get(r.ticker, (None, None))
        latest = latest_map.get(r.ticker, (end_price, None))

        stock = {
            "ticker": r.ticker,
            "name": r.name,
            "is_benchmark": r.is_benchmark,
            "sector": r.sector,
            "current_price": round(end_price, 2),
            "latest_close": round(latest[0], 2),
            "latest_close_date": latest[1],
            "period_start_price": round(start_price, 2),
            "period_return_pct": round(period_return_pct, 2),
            "cagr_pct": round(cagr_pct, 2),
            "high_52w": round(hi_lo[0], 2) if hi_lo[0] else None,
            "low_52w": round(hi_lo[1], 2) if hi_lo[1] else None,
        }

        # Filter by group if requested
        if group is not None:
            group_tickers = set()
            for g in groups:
                if g["id"] == group:
                    group_tickers = set(g["tickers"])
                    break
            if r.ticker not in group_tickers:
                continue

        stocks.append(stock)

    return {
        **_response_metadata(start_date, end_date),
        "return_basis": "split_adjusted_close",
        "data_freshness": freshness,
        "stocks": stocks,
        "groups": groups,
    }


@router.get("/api/investments/prices")
def get_prices(
    start: str | None = None,
    end: str | None = None,
    tickers: str | None = None,
    group: int | None = None,
    normalize: bool = True,
):
    """Time-series price data for charting."""
    start_date, end_date = _parse_date_range(start, end, max_days=INVESTMENTS_MAX_DAYS)

    with get_session() as session:
        ticker_list = _resolve_tickers(session, tickers, group)
        if not ticker_list:
            return {
                **_response_metadata(start_date, end_date),
                "series": {},
            }

        rows = session.execute(
            text("""
                SELECT w.ticker, dp.date, dp.close
                FROM investments.daily_prices dp
                JOIN investments.watchlist w ON w.id = dp.watchlist_id
                WHERE w.ticker = ANY(:tickers)
                  AND dp.date BETWEEN :start AND :end
                ORDER BY w.ticker, dp.date ASC
            """),
            {"tickers": ticker_list, "start": start_date, "end": end_date},
        ).fetchall()

    # Group by ticker
    series_data = defaultdict(lambda: {"dates": [], "closes": []})
    for r in rows:
        series_data[r.ticker]["dates"].append(r.date.isoformat())
        series_data[r.ticker]["closes"].append(float(r.close))

    # Normalize or return raw
    series = {}
    for ticker, data in series_data.items():
        closes = data["closes"]
        if normalize and closes:
            base = closes[0]
            values = [round((c / base) * 100, 2) for c in closes]
        else:
            values = [round(c, 2) for c in closes]

        series[ticker] = {
            "dates": data["dates"],
            "values": values,
        }

    return {
        **_response_metadata(start_date, end_date),
        "normalized": normalize,
        "series": series,
    }


@router.get("/api/investments/groups")
def get_groups():
    """All stock groups with member tickers."""
    with get_session() as session:
        groups = _fetch_groups(session)
    return {"groups": groups}


def _fetch_groups(session) -> list[dict]:
    """Fetch all groups with their member tickers."""
    rows = session.execute(text("""
        SELECT g.id, g.name, w.ticker
        FROM investments.stock_groups g
        LEFT JOIN investments.stock_group_members sgm ON sgm.group_id = g.id
        LEFT JOIN investments.watchlist w ON w.id = sgm.watchlist_id AND w.active = true
        ORDER BY g.name, w.ticker
    """)).fetchall()

    groups_map = {}
    for r in rows:
        if r.id not in groups_map:
            groups_map[r.id] = {"id": r.id, "name": r.name, "tickers": []}
        if r.ticker:
            groups_map[r.id]["tickers"].append(r.ticker)

    return list(groups_map.values())
