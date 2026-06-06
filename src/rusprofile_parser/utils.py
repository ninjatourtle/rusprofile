from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
MONEY_RE = re.compile(r"([+-]?(?=.*\d)[\d\s.,]*\d[\d\s.,]*)\s*(тыс\.?|млн|млрд)?\s*руб", re.IGNORECASE)


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
            return {
                **data,
                "cookies": [_normalize_cookie(cookie) for cookie in data["cookies"]],
            }
        if isinstance(data, list):
            return [_normalize_cookie(cookie) for cookie in data]
        raise ValueError(f"Unsupported JSON cookie format: {cookie_file}")
    return _load_netscape_cookies(text)


def _normalize_cookie(cookie: dict[str, Any]) -> dict[str, Any]:
    """Convert browser-extension JSON export to Playwright cookie dict."""
    name = cookie.get("name")
    if not name:
        raise ValueError("Cookie entry is missing 'name'")
    value = cookie.get("value")
    if value is None:
        raise ValueError(f"Cookie {name!r} is missing 'value'")

    domain = cookie.get("domain")
    path = cookie.get("path") or "/"
    normalized: dict[str, Any] = {
        "name": name,
        "value": value,
        "path": path,
    }
    if domain:
        normalized["domain"] = domain
    else:
        normalized["url"] = _cookie_url(".rusprofile.ru", path)

    if cookie.get("httpOnly") is not None:
        normalized["httpOnly"] = bool(cookie["httpOnly"])
    if cookie.get("secure") is not None:
        normalized["secure"] = bool(cookie["secure"])

    if not cookie.get("session"):
        expires = cookie.get("expires", cookie.get("expirationDate"))
        if expires is not None:
            normalized["expires"] = int(float(expires))

    same_site = _normalize_same_site(cookie.get("sameSite"))
    if same_site:
        normalized["sameSite"] = same_site

    return normalized


def _normalize_same_site(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"", "unspecified", "null"}:
            return None
        if lowered in {"no_restriction", "none"}:
            return "None"
        if lowered == "lax":
            return "Lax"
        if lowered == "strict":
            return "Strict"
        if value in {"Strict", "Lax", "None"}:
            return value
    return None


def _cookie_url(domain: str, path: str) -> str:
    host = domain.removeprefix(".")
    if domain.startswith(".") and not host.startswith("www."):
        host = f"www.{host}"
    return f"https://{host}{path or '/'}"


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
    normalized_number = re.sub(r"\s+", "", raw).replace(",", ".").strip(".")
    if not normalized_number:
        return None
    number = float(normalized_number)
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
        lowered = value.lower()
        if _is_ignored_website(lowered) or prefix == "@":
            continue
        return value
    return None


def extract_domain_from_website(website: str | None) -> str | None:
    if not website:
        return None
    value = website.strip()
    if not value:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        value = f"http://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip(".").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    if _is_ignored_website(host):
        return None
    return host


def check_domain_registration(domain: str | None, timeout: float = 4.0) -> str | None:
    if not domain:
        return None
    normalized = domain.strip().strip(".").lower()
    if not normalized:
        return None
    try:
        socket.getaddrinfo(normalized, None)
        return "registered"
    except socket.gaierror:
        pass
    except OSError:
        pass

    whois_response = _whois_lookup(normalized, timeout=timeout)
    if whois_response is None:
        return "unknown"
    lowered = whois_response.lower()
    if any(marker in lowered for marker in _whois_available_markers(normalized)):
        return "available"
    if any(marker in lowered for marker in ("domain name:", "domain:", "registrar:", "created:", "paid-till:", "nserver:")):
        return "registered"
    return "unknown"


def _whois_lookup(domain: str, timeout: float) -> str | None:
    server = _whois_server(domain)
    if not server:
        return None
    try:
        with socket.create_connection((server, 43), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(f"{domain}\r\n".encode("utf-8"))
            chunks: list[bytes] = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="ignore")
    except OSError:
        return None


def _whois_server(domain: str) -> str | None:
    tld = domain.rsplit(".", 1)[-1]
    return {
        "ru": "whois.tcinet.ru",
        "рф": "whois.tcinet.ru",
        "com": "whois.verisign-grs.com",
        "net": "whois.verisign-grs.com",
        "org": "whois.pir.org",
    }.get(tld)


def _whois_available_markers(domain: str) -> tuple[str, ...]:
    tld = domain.rsplit(".", 1)[-1]
    if tld in {"ru", "рф"}:
        return ("no entries found", "not found")
    return (
        "no match for",
        "not found",
        "no data found",
        "domain not found",
        "status: free",
    )


def _is_ignored_website(value: str) -> bool:
    ignored_domains = (
        "rusprofile.ru",
        "yandex.ru",
        "yandex.net",
        "google.com",
        "google.ru",
        "gstatic.com",
        "clarity.ms",
        "baturin.ru",
    )
    return any(domain in value.lower() for domain in ignored_domains)
