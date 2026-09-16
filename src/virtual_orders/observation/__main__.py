"""`python -m virtual_orders.observation report --day YYYY-MM-DD [--format markdown|json]`
and `python -m virtual_orders.observation summary --from YYYY-MM-DD --to YYYY-MM-DD [--format markdown|json]`.

Read-only (Plan 4, D70, D73): reads DATABASE_URL only, never provider credentials, never writes; the whole document is
read in one read-only REPEATABLE READ snapshot (D61). Exit codes: 0 ok; 1 internal error (INTERNAL_ERROR, or
DATABASE_ERROR for any SQLAlchemy error that is not a connection failure); 2 usage or CONFIG_INVALID;
3 OBSERVATION_REQUEST_INVALID; 4 DATABASE_UNAVAILABLE (OperationalError, InterfaceError or a pool TimeoutError, as
/health maps them). Errors go to stderr as one JSON line with fixed codes or an exception type, never a message.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from typing import TextIO

from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from virtual_orders.marketdata.asof import acquire_data_as_of
from virtual_orders.observation.render import render_json, render_markdown
from virtual_orders.readmodels.observation import (
    ObservationReport,
    ObservationSummary,
    build_observation_report,
    build_observation_summary,
    observation_snapshot,
)
from virtual_orders.readmodels.observation_window import ObservationRequestInvalid, session_window, summary_sessions
from virtual_orders.storage.database import make_engine

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INVALID_REQUEST = 3
EXIT_DATABASE_UNAVAILABLE = 4
# The /health mapping (readmodels/health.py): only connection failures mean the database is unavailable.
DATABASE_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, PoolTimeoutError)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m virtual_orders.observation")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="one NYSE session")
    report.add_argument("--day", type=date.fromisoformat, required=True)
    summary = commands.add_parser("summary", help="every NYSE session of a date range")
    summary.add_argument("--from", dest="first", type=date.fromisoformat, required=True)
    summary.add_argument("--to", dest="last", type=date.fromisoformat, required=True)
    for command in (report, summary):
        command.add_argument("--format", choices=("markdown", "json"), default="markdown")
    return parser


def _error(stream: TextIO, payload: Mapping[str, object]) -> None:
    print(json.dumps(dict(payload)), file=stream)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[[str], Engine] = make_engine,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err
    args = _parser().parse_args(argv)  # argparse exits 2 with a usage line on invalid arguments
    url = (os.environ if environ is None else environ).get("DATABASE_URL", "").strip()
    if not url:
        _error(stderr, {"error": "CONFIG_INVALID", "errors": ["MISSING:DATABASE_URL"]})
        return EXIT_USAGE
    document: ObservationReport | ObservationSummary
    try:
        engine = engine_factory(url)
    except Exception as exc:  # noqa: BLE001 - a malformed URL is reported by type only
        _error(stderr, {"error": "CONFIG_INVALID", "errors": ["INVALID:DATABASE_URL"], "type": type(exc).__name__})
        return EXIT_USAGE
    try:
        as_of = acquire_data_as_of(engine)
        if args.command == "report":
            window = session_window(args.day, as_of)
            with observation_snapshot(engine) as conn:  # D61: one read-only REPEATABLE READ snapshot
                document = build_observation_report(conn, window)
        else:
            windows = summary_sessions(args.first, args.last, as_of)
            with observation_snapshot(engine) as conn:
                document = build_observation_summary(conn, windows)
    except ObservationRequestInvalid as exc:
        _error(stderr, {"error": "OBSERVATION_REQUEST_INVALID", "errors": list(exc.codes)})
        return EXIT_INVALID_REQUEST
    except DATABASE_UNAVAILABLE_ERRORS as exc:
        _error(stderr, {"error": "DATABASE_UNAVAILABLE", "type": type(exc).__name__})
        return EXIT_DATABASE_UNAVAILABLE
    except SQLAlchemyError as exc:  # a SQL defect (ProgrammingError, DataError, ...), never "database unavailable"
        _error(stderr, {"error": "DATABASE_ERROR", "type": type(exc).__name__})
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - never a traceback: its text could carry data or connection details
        _error(stderr, {"error": "INTERNAL_ERROR", "type": type(exc).__name__})
        return EXIT_INTERNAL
    finally:
        engine.dispose()
    stdout.write(render_json(document) + "\n" if args.format == "json" else render_markdown(document))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
