import csv
import json

from rusprofile_parser.models import CompanyResult
from rusprofile_parser.output import append_result, prepare_output, write_results


def make_result(name: str, matched: bool) -> CompanyResult:
    return CompanyResult(
        name=name,
        url=f"https://www.rusprofile.ru/id/{name}",
        inn=None,
        ogrn=None,
        registration_date="01.01.2020",
        director_appointment_date="01.01.2020",
        revenue=10,
        profit=1,
        email=None,
        website=None,
        matched=matched,
        reasons=["ok"],
    )


def test_write_results_stores_only_matched_jsonl(tmp_path) -> None:
    output = tmp_path / "results.jsonl"

    write_results(output, [make_result("skip", False), make_result("save", True)])

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["name"] for row in rows] == ["save"]


def test_append_result_keeps_csv_header_and_appends_immediately(tmp_path) -> None:
    output = tmp_path / "results.csv"

    prepare_output(output)
    append_result(output, make_result("first", True))
    append_result(output, make_result("second", True))

    with output.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert [row["name"] for row in rows] == ["first", "second"]
