from pathlib import Path

from rusprofile_parser.utils import extract_email, extract_website, first_date_after, load_cookies, parse_money_after, parse_proxy


def test_parse_proxy_host_port_user_password() -> None:
    assert parse_proxy("fproxy.site:16424:user:pass") == {
        "server": "http://fproxy.site:16424",
        "username": "user",
        "password": "pass",
    }


def test_parse_money_after_scales_values() -> None:
    assert parse_money_after("Выручка", "Выручка 12,5 млн руб") == 12_500_000
    assert parse_money_after("Прибыль", "Прибыль +42 тыс. руб") == 42_000


def test_extractors_and_dates() -> None:
    text = "Дата регистрации 01.02.2020 Руководитель назначен 01.02.2020 e@mail.ru site.ru"
    assert first_date_after("Дата регистрации", text) == "01.02.2020"
    assert first_date_after("назнач", text) == "01.02.2020"
    assert extract_email(text) == "e@mail.ru"
    assert extract_website(text) == "site.ru"


def test_load_netscape_cookies(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(".rusprofile.ru\tTRUE\t/\tFALSE\t0\tsid\t123\n", encoding="utf-8")
    assert load_cookies(cookie_file) == [
        {"name": "sid", "value": "123", "domain": ".rusprofile.ru", "path": "/", "secure": False, "expires": 0}
    ]
