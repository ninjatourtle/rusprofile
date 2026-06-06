from pathlib import Path

from rusprofile_parser.cli import unique_output_path


def test_unique_output_path_adds_run_id(tmp_path: Path) -> None:
    output = tmp_path / "results.csv"

    first = unique_output_path(output, "20260606_143015")
    first.write_text("", encoding="utf-8")
    second = unique_output_path(output, "20260606_143015")

    assert first.name == "results_20260606_143015.csv"
    assert second.name == "results_20260606_143015_2.csv"
