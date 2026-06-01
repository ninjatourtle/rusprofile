from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import CompanyResult


def prepare_output(output: Path) -> None:
    if output.suffix.lower() == ".csv":
        with output.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(CompanyResult.__dataclass_fields__.keys()))
            writer.writeheader()
        return
    output.write_text("", encoding="utf-8")


def append_result(output: Path, result: CompanyResult) -> None:
    if output.suffix.lower() == ".csv":
        with output.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(CompanyResult.__dataclass_fields__.keys()))
            writer.writerow(result.to_dict())
        return
    with output.open("a", encoding="utf-8") as file:
        file.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")


def write_results(output: Path, results: list[CompanyResult]) -> None:
    prepare_output(output)
    for result in results:
        if result.matched:
            append_result(output, result)
