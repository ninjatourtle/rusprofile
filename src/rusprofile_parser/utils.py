from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
MONEY_RE = re.compile(r"([+-]?[\d\s.,]+)\s*(тыс\.?|млн|млрд)?\s*руб", re.IGNORECASE)


def parse_proxy(proxy: str | None) -> dict[str, str] | None:
    """Convert host:port:user:pass or URL proxy notation to Playwright format."""
    if not proxy:
        return None
    value = proxy.strip()
    if not value:
        return None
    if "://" in value:
        scheme, rest = value.split("://", 1)
        auth, separator, hostport = rest.rpartition("@")
        if separator:
            username, _, password = auth.partition(":")
            return {"server": f"{scheme}://{hostport}", "username": username, "password": password}
        return {"server": value}
    host, port, username, password = value.split(":", 3)
    return {"server": f"http://{host}:{port}", "username": username, "password": password}


def load_cookies(cookie_file: Path) -> list[dict[str, Any]] | dict[str, Any]:
    """Load Playwright storage_state, exported JSON cookies, or Netscape cookies.txt."""
    text = cookie_file.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict) and "cookies" in data:
            return data
        if isinstance(data, list):
            return [_normalize_cookie(cookie) for cookie in data]
        raise ValueError(f"Unsupported JSON cookie format: {cookie_file}")
    return _load_netscape_cookies(text)


def _normalize_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(cookie)
    if "expirationDate" in normalized and "expires" not in normalized:
        normalized["expires"] = normalized.pop("expirationDate")
    if "sameSite" in normalized and normalized["sameSite"] not in {"Strict", "Lax", "None"}:
        normalized["sameSite"] = "Lax"
    if "domain" not in normalized:
        normalized["domain"] = ".rusprofile.ru"
    if "path" not in normalized:
        normalized["path"] = "/"
    return normalized


def _load_netscape_cookies(text: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _include_subdomains, path, secure, expires, name, value = parts
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure.upper() == "TRUE",
                "expires": int(expires) if expires.isdigit() else -1,
            }
        )
    return cookies


def first_date_after(label: str, text: str) -> str | None:
    match = re.search(rf"{re.escape(label)}[^\d]{{0,80}}(\d{{2}}\.\d{{2}}\.\d{{4}})", text, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else None


def parse_money_after(label: str, text: str) -> int | None:
    label_match = re.search(re.escape(label), text, re.IGNORECASE)
    if not label_match:
        return None
    snippet = text[label_match.end() : label_match.end() + 220]
    money_match = MONEY_RE.search(snippet)
    if not money_match:
        return None
    raw, scale = money_match.groups()
    number = float(raw.replace(" ", "").replace(",", "."))
    multiplier = 1
    if scale:
        normalized = scale.lower().replace(".", "")
        if normalized == "тыс":
            multiplier = 1_000
        elif normalized == "млн":
            multiplier = 1_000_000
        elif normalized == "млрд":
            multiplier = 1_000_000_000
    return int(number * multiplier)


def extract_email(text: str) -> str | None:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}", text)
    return match.group(0) if match else None


def extract_website(text: str) -> str | None:
    for match in re.finditer(r"\b(?:https?://)?(?:www\.)?[\w-]+\.(?:ru|com|net|org|рф)\b", text, re.IGNORECASE):
        value = match.group(0)
        prefix = text[max(0, match.start() - 1) : match.start()]
        if "rusprofile.ru" in value or prefix == "@":
            continue
        return value
    return None
