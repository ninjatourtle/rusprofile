from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from .parser import RusprofileParser

DEFAULT_PROXY = "fproxy.site:16424:kAaRaA:muf4TAx6ABEG"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parser for rusprofile.ru advanced search results")
    parser.add_argument("--proxy", default=os.getenv("RUSPROFILE_PROXY", DEFAULT_PROXY), help="HTTP proxy as host:port:user:pass or URL")
    parser.add_argument("--cookies", type=Path, default=Path(os.getenv("RUSPROFILE_COOKIES", "cookies.json")), help="Path to cookies.json/storage_state.json/cookies.txt")
    parser.add_argument("--output", type=Path, default=Path("output/rusprofile_results.jsonl"), help="Output .jsonl or .csv path")
    parser.add_argument("--all-output", type=Path, default=Path("output/rusprofile_all_results.csv"), help="CSV path for all inspected companies")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of companies to inspect")
    parser.add_argument("--headed", action="store_true", help="Run Chromium with visible UI")
    parser.add_argument("--slow-mo", type=int, default=0, help="Playwright slow motion in milliseconds")
    parser.add_argument("--timeout", type=int, default=30_000, help="Default Playwright timeout in milliseconds")
    parser.add_argument(
        "--require-both-missing-contacts",
        action="store_true",
        help="Require both website and email to be absent. By default matches absence of either website or email.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = unique_output_path(args.output, run_id)
    all_output = unique_output_path(args.all_output, run_id)
    parser = RusprofileParser(
        proxy=args.proxy,
        cookies=args.cookies,
        headless=not args.headed,
        slow_mo_ms=args.slow_mo,
        timeout_ms=args.timeout,
        require_both_missing_contacts=args.require_both_missing_contacts,
    )
    results = parser.run(output=output, limit=args.limit, all_output=all_output)
    matched = sum(1 for result in results if result.matched)
    print(f"Проверено компаний: {len(results)}")
    print(f"Подходят под критерии: {matched}")
    print(f"Файл результата: {output}")
    print(f"CSV всех проверенных: {all_output}")
    return 0


def unique_output_path(path: Path, run_id: str) -> Path:
    candidate = path.with_name(f"{path.stem}_{run_id}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{run_id}_{counter}{path.suffix}")
        counter += 1
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
