"""`python -m virtual_orders.worker [run | rebuild-projections [--order-id UUID ...]]` (spec 5.3 and 6, D20).

The only worker module that touches the composition root.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO
from uuid import UUID

from virtual_orders.bootstrap import build_services
from virtual_orders.config import ConfigError, Settings, load_settings
from virtual_orders.evaluator.rebuild import rebuild_all_projections
from virtual_orders.services import Services
from virtual_orders.worker.runner import run_worker
from virtual_orders.worker.schedule import interval_config_errors

EXIT_CONFIG = 2
EXIT_REBUILD_PROBLEMS = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m virtual_orders.worker")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="start the scheduler (default)")
    rebuild = commands.add_parser("rebuild-projections", help="rebuild and verify order_state from history (D2)")
    rebuild.add_argument("--order-id", dest="order_ids", action="append", type=UUID, default=None)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    services_factory: Callable[[Settings], Services] = build_services,
    worker: Callable[[Services], int] = run_worker,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    stdout = sys.stdout if out is None else out
    stderr = sys.stderr if err is None else err
    args = _parser().parse_args(argv)
    try:
        settings = load_settings(os.environ if environ is None else environ)
        if args.command in (None, "run"):
            interval_errors = interval_config_errors(settings.eval_interval_minutes)
            if interval_errors:  # D36: reported like any configuration error, before anything is built
                raise ConfigError(interval_errors)
        services = services_factory(settings)
    except ConfigError as exc:  # errors name variables, never values (D13)
        print(json.dumps({"error": "CONFIG_INVALID", "errors": exc.errors}), file=stderr)
        return EXIT_CONFIG
    if args.command == "rebuild-projections":
        try:
            report = rebuild_all_projections(services.engine, args.order_ids)
        finally:
            services.close()
        problems = {str(order_id): result for order_id, result in report.items() if result != "OK"}
        print(json.dumps({
            "orders": len(report), "results": dict(sorted(Counter(report.values()).items())),
            "problems": dict(sorted(problems.items())),
        }, sort_keys=True), file=stdout)
        return EXIT_REBUILD_PROBLEMS if problems else 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return worker(services)


if __name__ == "__main__":
    raise SystemExit(main())
