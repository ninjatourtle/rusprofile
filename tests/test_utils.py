from pathlib import Path

from rusprofile_parser.utils import (
    extract_domain_from_website,
    extract_email,
    extract_website,
    first_date_after,
    load_cookies,
    parse_money_after,
    parse_proxy,
)


def test_parse_proxy_host_port_user_password() -> None:
    assert parse_proxy("fproxy.site:16424:user:pass") == {
        "server": "http://fproxy.site:16424",
        "username": "user",
        "password": "pass",
    }


def test_parse_money_after_scales_values() -> None:
    assert parse_money_after("Выручка", "Выручка 12,5 млн руб") == 12_500_000
    assert parse_money_after("Прибыль", "Прибыль +42 тыс. руб") == 42_000


def test_parse_money_after_ignores_empty_money_text() -> None:
    assert parse_money_after("Выручка", "Выручка \n руб") is None


def test_extractors_and_dates() -> None:
    text = "Дата регистрации 01.02.2020 Руководитель назначен 01.02.2020 e@mail.ru site.ru"
    assert first_date_after("Дата регистрации", text) == "01.02.2020"
    assert first_date_after("назнач", text) == "01.02.2020"
    assert extract_email(text) == "e@mail.ru"
    assert extract_website(text) == "site.ru"


def test_extract_website_ignores_rusprofile() -> None:
    text = "Rusprofile.ru info email test@example.com сайт arielmetal.ru"
    assert extract_website(text) == "arielmetal.ru"


def test_extract_domain_from_website() -> None:
    assert extract_domain_from_website("http://www.puyang.ru/") == "puyang.ru"
    assert extract_domain_from_website("https://sub.example.com/path") == "sub.example.com"
    assert extract_domain_from_website("https://baturin.ru?rp") is None


def test_load_netscape_cookies(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(".rusprofile.ru\tTRUE\t/\tFALSE\t0\tsid\t123\n", encoding="utf-8")
    assert load_cookies(cookie_file) == [
        {"name": "sid", "value": "123", "domain": ".rusprofile.ru", "path": "/", "secure": False, "expires": 0}
    ]


def test_load_browser_extension_cookies(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        """
        [
          {
            "domain": ".rusprofile.ru",
            "expirationDate": 1807170725,
            "hostOnly": false,
            "httpOnly": true,
            "name": "sessid",
            "path": "/",
            "sameSite": "no_restriction",
            "secure": true,
            "session": false,
            "value": "abc"
          },
          {
            "domain": "www.rusprofile.ru",
            "name": "activeNow",
            "path": "/",
            "sameSite": null,
            "session": true,
            "value": "today"
          }
        ]
        """,
        encoding="utf-8",
    )
    cookies = load_cookies(cookie_file)
    assert len(cookies) == 2
    assert cookies[0] == {
        "name": "sessid",
        "value": "abc",
        "domain": ".rusprofile.ru",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "expires": 1807170725,
        "sameSite": "None",
    }
    assert cookies[1] == {
        "name": "activeNow",
        "value": "today",
        "domain": "www.rusprofile.ru",
        "path": "/",
    }
