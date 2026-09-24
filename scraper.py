"""
Auction Scanner — scrapes EU auction / forced-sale platforms, stores them in
SQLite, scores them and reports on them.

The scrapers live in sources/ (one module per country); this file is the
command line. Supported platforms: python scraper.py --list-sources

Usage:
  python scraper.py                      # scrape all default sources, write report
  python scraper.py --country PT         # all Portuguese sources
  python scraper.py --country ES,FR      # several countries
  python scraper.py --source eleiloes    # one source
  python scraper.py --report-only        # re-generate report from the DB
  python scraper.py --health             # which scrapers are working
  python scraper.py --list-sources
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from common import COUNTRY_NAMES, configure_http, parse_price
from db import DB_PATH, connect, init_db, mark_duplicates, source_health, upsert_listing
from report import generate_report, print_console_summary, print_sealed_bid_summary
from scoring import categorize as _categorize
from scoring import score as _score_fn
from sources import REGISTRY, load_all, run_source, sources_for

load_all()

# Backwards compatibility for code that imported these from scraper.py.
__all__ = ["main", "DB_PATH", "init_db", "upsert_listing", "investment_score",
           "generate_report", "print_console_summary", "_parse_euro"]
_parse_euro = parse_price
for _src in REGISTRY.values():
    globals()[_src.func.__name__] = _src.func


def investment_score(item: dict) -> tuple[float, list[str]]:
    return _score_fn(item)


def _country_list(value: str) -> list[str] | None:
    """--country PT / PT,ES / all  →  ["PT"] / ["PT", "ES"] / None (every country)."""
    if value.lower() == "all":
        return None
    codes = [c.strip().upper() for c in value.split(",") if c.strip()]
    bad = [c for c in codes if c not in COUNTRY_NAMES and c != "EU"]
    if bad or not codes:
        raise argparse.ArgumentTypeError(
            f"unknown country {', '.join(bad) or value!r}; use {', '.join(COUNTRY_NAMES)} or all")
    return codes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EU Auction Scanner")
    parser.add_argument("--source", choices=sorted(REGISTRY) + ["all"], default="all", metavar="NAME",
                        help="One source (see --list-sources), or all")
    parser.add_argument("--country", type=_country_list, default=False,
                        help="Scrape every default source of these countries (PT, PT,ES or all)")
    parser.add_argument("--max-price", type=float, default=None)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--analyze", action="store_true", help="Run LLM analysis on top listings")
    parser.add_argument("--analyze-category", default="imoveis", choices=["imoveis", "ouro_joias", "outros"])
    parser.add_argument("--notify", action="store_true", help="Send email alerts for high-scoring listings")
    parser.add_argument("--digest", action="store_true", help="Send weekly top-15 digest email")
    parser.add_argument("--check-active", action="store_true", help="Check which Citius listings are still active")
    parser.add_argument("--cartas", action="store_true", help="Generate proposal PDFs for active Citius listings")
    parser.add_argument("--cartas-top", type=int, default=15, help="Number of top listings to generate cartas for")
    parser.add_argument("--sealed-bid", action="store_true", help="Show only venda por propostas em carta fechada listings")
    parser.add_argument("--dashboard", action="store_true", help="Launch web dashboard after scraping")
    parser.add_argument("--health", action="store_true", help="Show per-source scrape health and exit")
    parser.add_argument("--list-sources", action="store_true", help="List every source and exit")
    return parser


def select_sources(args) -> list:
    if args.country is not False:
        return sources_for(args.country)
    if args.source == "all":
        return sources_for(None)
    return [REGISTRY[args.source]]


def print_health(db):
    rows = source_health(db, REGISTRY)
    print(f"\n{'Source':<20} {'Country':<8} {'State':<13} {'Last run':<17} {'Count':>6} {'Failing':>8}  Message")
    print("-" * 100)
    for h in rows:
        src = REGISTRY.get(h["source"])
        print(f"{h['source']:<20} {(src.country if src else '?'):<8} {h['state']:<13} "
              f"{(h['last_run'] or '-')[:16]:<17} {h['last_count'] if h['last_count'] is not None else '-':>6} "
              f"{h['failing_runs']:>8}  {(h['last_message'] or '')[:60]}")
    print()


def print_sources():
    for s in sources_for(None, include_optional=True):
        flag = "" if s.default else "  (only with --source)"
        print(f"{s.country:<3} {s.name:<20} {s.description}{flag}")


def _check_active(db):
    from cartas import check_citius_active
    proc_trib = {}
    for r in db.execute("SELECT raw_json FROM listings WHERE source='citius' AND raw_json IS NOT NULL"):
        raw = json.loads(r[0] or "{}")
        proc, trib = raw.get("processo", ""), raw.get("tribunal", "")
        if proc and trib:
            proc_trib[proc.split(",")[0].strip()] = trib
    print(f"\nChecking {len(proc_trib)} Citius processes...")
    estados = check_citius_active(proc_trib)
    active = sum(1 for e in estados.values() if "em venda" in e.lower())
    print(f"\n{active} of {len(estados)} are active ('Em venda')")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    if args.list_sources:
        print_sources()
        return 0

    from config import load_config
    cfg = load_config()
    configure_http(cfg.get("proxies"))
    max_price = args.max_price or cfg.get("max_price", 50000)
    max_bid = args.max_price or cfg.get("max_bid", max_price)
    filters = cfg.get("filters", {})
    report_cfg = cfg.get("report", {})

    db = connect()
    try:
        if args.health:
            print_health(db)
            return 0

        if not args.report_only and not args.analyze:
            for source in select_sources(args):
                run_source(db, source, max_price=max_price, config=cfg)
            mark_duplicates(db)

        report_path = generate_report(
            db, max_price=max_price, max_bid=max_bid, filters=filters,
            out_dir=report_cfg.get("out_dir"), desktop_copy=report_cfg.get("desktop_copy", True),
            known_sources=REGISTRY)

        if args.sealed_bid:
            print_sealed_bid_summary(db, filters=filters)

        if args.analyze:
            from analysis import analyze_with_llm
            analysis_path = analyze_with_llm(db, max_price=max_price,
                                             category=args.analyze_category, filters=filters)
            if analysis_path:
                print(f"Analysis: {analysis_path}")

        notify_cfg = cfg.get("notifications", {})
        if args.notify or notify_cfg.get("enabled"):
            from notifications import send_alerts
            send_alerts(db, {**notify_cfg, "enabled": True}, max_price=max_price, filters=filters)

        from telegram_alert import alert_new_listings
        alert_new_listings(db, cfg)

        if args.digest:
            from notifications import send_weekly_digest
            send_weekly_digest(db, notify_cfg, max_price=max_price, filters=filters)

        if args.check_active:
            _check_active(db)

        if args.cartas:
            from cartas import generate_cartas
            generate_cartas(
                db, _score_fn, _categorize, cfg.get("proponente", {}),
                os.path.join(os.path.dirname(DB_PATH), "cartas"),
                max_price=max_price, top_n=args.cartas_top, filters=filters,
            )

        print_console_summary(db, max_price=max_price, filters=filters, known_sources=REGISTRY)
        print(f"\nFull report: {report_path}")

        # Kept for anything still reading the old flag; alerts use alert_log now.
        db.execute("UPDATE listings SET is_new = 0")
        db.commit()
    finally:
        db.close()

    if args.dashboard:
        from dashboard import main as run_dashboard
        run_dashboard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
