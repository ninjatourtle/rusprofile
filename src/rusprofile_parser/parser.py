from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Iterable

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError, sync_playwright

from .models import CompanyCandidate, CompanyResult
from .utils import extract_email, extract_website, first_date_after, load_cookies, parse_money_after, parse_proxy

BASE_URL = "https://www.rusprofile.ru"
SEARCH_URL = f"{BASE_URL}/search-advanced"


class RusprofileParser:
    def __init__(
        self,
        proxy: str | None,
        cookies: Path | None,
        headless: bool = True,
        slow_mo_ms: int = 0,
        timeout_ms: int = 30_000,
        require_both_missing_contacts: bool = False,
    ) -> None:
        self.proxy = proxy
        self.cookies = cookies
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.timeout_ms = timeout_ms
        self.require_both_missing_contacts = require_both_missing_contacts

    def run(self, output: Path, limit: int | None = None) -> list[CompanyResult]:
        output.parent.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                proxy=parse_proxy(self.proxy),
            )
            context = browser.new_context(
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
            )
            context.set_default_timeout(self.timeout_ms)
            self._load_context_cookies(context)
            page = context.new_page()
            self._apply_filters(page)
            candidates = self._collect_all_candidates(page, limit)
            results = [self._inspect_company(context, candidate) for candidate in candidates]
            self._write_results(output, results)
            context.close()
            browser.close()
            return results

    def _load_context_cookies(self, context: BrowserContext) -> None:
        if not self.cookies or not self.cookies.exists():
            return
        data = load_cookies(self.cookies)
        if isinstance(data, dict):
            context.add_cookies(data.get("cookies", []))
        else:
            context.add_cookies(data)

    def _apply_filters(self, page: Page) -> None:
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        self._dismiss_overlays(page)
        self._set_status_active_only(page)
        self._select_activity(page, "46.9")
        self._select_region(page, "Москва")
        self._set_employee_min(page, "5")
        self._submit_filters(page)

    def _dismiss_overlays(self, page: Page) -> None:
        for text in ("Принять", "Понятно", "Согласен", "Закрыть"):
            button = page.get_by_text(text, exact=True)
            if self._visible(button):
                button.first.click()
                page.wait_for_timeout(300)

    def _set_status_active_only(self, page: Page) -> None:
        statuses = {
            "Действующая": True,
            "В процессе реорганизации": False,
            "В процессе ликвидации": False,
            "В процессе банкротства": False,
            "Ликвидированная": False,
        }
        for label, expected in statuses.items():
            checkbox = page.locator("label", has_text=label).locator("input[type=checkbox]").first
            if checkbox.count() == 0:
                checkbox = page.get_by_label(label).first
            if checkbox.count() and checkbox.is_checked() != expected:
                page.locator("label", has_text=label).first.click()

    def _select_activity(self, page: Page, okved: str) -> None:
        self._open_filter(page, "Вид деятельности")
        search = self._find_active_input(page, preferred_placeholder="Космический туризм")
        search.fill(okved)
        page.wait_for_timeout(1000)
        option = page.locator("label, li, div, button", has_text=re.compile(rf"\b{re.escape(okved)}\b")).last
        option.click()
        self._click_done(page)

    def _select_region(self, page: Page, region: str) -> None:
        self._open_filter(page, "Регион")
        search = self._find_active_input(page)
        search.fill(region)
        page.wait_for_timeout(1000)
        option = page.locator("label, li, div, button", has_text=re.compile(rf"\b{re.escape(region)}\b", re.IGNORECASE)).last
        option.click()
        self._click_done(page)

    def _set_employee_min(self, page: Page, minimum: str) -> None:
        self._open_filter(page, "Количество сотрудников")
        section = self._expanded_section(page, "Количество сотрудников")
        inputs = section.locator("input") if section.count() else page.locator("input:visible")
        for index in range(inputs.count()):
            current = inputs.nth(index)
            placeholder = (current.get_attribute("placeholder") or "").lower()
            aria = (current.get_attribute("aria-label") or "").lower()
            name = (current.get_attribute("name") or "").lower()
            if any(marker in f"{placeholder} {aria} {name}" for marker in ("от", "from", "min")) or index == 0:
                current.fill(minimum)
                break
        self._click_done(page, required=False)

    def _submit_filters(self, page: Page) -> None:
        for text in ("Показать", "Найти", "Применить"):
            button = page.get_by_role("button", name=re.compile(text, re.IGNORECASE))
            if self._visible(button):
                button.first.click()
                page.wait_for_load_state("domcontentloaded")
                return
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)

    def _open_filter(self, page: Page, name: str) -> None:
        target = page.get_by_text(name, exact=True).first
        if not self._visible(target):
            target = page.locator("button, div, span, label", has_text=name).first
        target.click()
        page.wait_for_timeout(500)

    def _find_active_input(self, page: Page, preferred_placeholder: str | None = None) -> Locator:
        if preferred_placeholder:
            by_placeholder = page.get_by_placeholder(preferred_placeholder)
            if self._visible(by_placeholder):
                return by_placeholder.first
        visible_inputs = page.locator("input:visible")
        for index in range(visible_inputs.count() - 1, -1, -1):
            field = visible_inputs.nth(index)
            input_type = (field.get_attribute("type") or "text").lower()
            if input_type in {"text", "search", ""}:
                return field
        raise RuntimeError("Не найдено видимое поле поиска фильтра")

    def _expanded_section(self, page: Page, heading: str) -> Locator:
        return page.locator("div, section, form", has=page.get_by_text(heading, exact=True)).last

    def _click_done(self, page: Page, required: bool = True) -> None:
        done = page.get_by_text("Готово", exact=True)
        if self._visible(done):
            done.last.click()
            page.wait_for_timeout(500)
        elif required:
            raise RuntimeError("Не найдена кнопка 'Готово'")

    def _collect_all_candidates(self, page: Page, limit: int | None) -> list[CompanyCandidate]:
        seen: set[str] = set()
        candidates: list[CompanyCandidate] = []
        while True:
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
            for candidate in self._extract_candidates(page):
                if candidate.url in seen:
                    continue
                seen.add(candidate.url)
                candidates.append(candidate)
                if limit and len(candidates) >= limit:
                    return candidates
            if not self._go_next_page(page):
                break
        return candidates

    def _extract_candidates(self, page: Page) -> Iterable[CompanyCandidate]:
        links = page.locator('a[href^="/id/"], a[href*="/id/"]')
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href")
            if not href:
                continue
            url = href if href.startswith("http") else f"{BASE_URL}{href}"
            text = link.inner_text().strip()
            card_text = link.locator("xpath=ancestor::*[self::div or self::article or self::li][1]").inner_text(timeout=3000)
            inn_match = re.search(r"ИНН\s*(\d+)", card_text)
            ogrn_match = re.search(r"ОГРН\s*(\d+)", card_text)
            name = text or card_text.splitlines()[0]
            yield CompanyCandidate(
                name=name,
                url=url,
                inn=inn_match.group(1) if inn_match else None,
                ogrn=ogrn_match.group(1) if ogrn_match else None,
            )

    def _go_next_page(self, page: Page) -> bool:
        for selector in (
            'a[rel="next"]',
            'a:has-text("Следующая")',
            'button:has-text("Показать ещё")',
            'button:has-text("Показать все")',
        ):
            control = page.locator(selector)
            if self._visible(control):
                before = page.url
                control.first.click()
                page.wait_for_timeout(1500)
                if page.url != before:
                    page.wait_for_load_state("domcontentloaded")
                return True
        return False

    def _inspect_company(self, context: BrowserContext, candidate: CompanyCandidate) -> CompanyResult:
        page = context.new_page()
        page.goto(candidate.url, wait_until="domcontentloaded")
        page.wait_for_timeout(700)
        text = page.locator("body").inner_text()
        registration_date = first_date_after("Дата регистрации", text) or first_date_after("ОГРН", text)
        appointment_date = first_date_after("назнач", text) or first_date_after("Руководитель", text)
        revenue = parse_money_after("Выручка", text)
        profit = parse_money_after("Прибыль", text) or parse_money_after("Чистая прибыль", text)
        email = extract_email(text)
        website = extract_website(text)
        reasons: list[str] = []
        if registration_date and appointment_date and registration_date == appointment_date:
            reasons.append("дата регистрации совпадает с датой назначения руководителя")
        if revenue is not None and revenue > 0:
            reasons.append("выручка положительная")
        if profit is not None and profit > 0:
            reasons.append("прибыль положительная")
        contacts_missing = not website and not email if self.require_both_missing_contacts else (not website or not email)
        if contacts_missing:
            reasons.append("нет сайта или почты" if not self.require_both_missing_contacts else "нет сайта и почты")
        matched = bool(
            contacts_missing
            and registration_date
            and appointment_date
            and registration_date == appointment_date
            and revenue is not None
            and revenue > 0
            and profit is not None
            and profit > 0
        )
        page.close()
        return CompanyResult(
            name=candidate.name,
            url=candidate.url,
            inn=candidate.inn,
            ogrn=candidate.ogrn,
            registration_date=registration_date,
            director_appointment_date=appointment_date,
            revenue=revenue,
            profit=profit,
            email=email,
            website=website,
            matched=matched,
            reasons=reasons,
        )

    def _write_results(self, output: Path, results: list[CompanyResult]) -> None:
        matched = [result for result in results if result.matched]
        if output.suffix.lower() == ".csv":
            with output.open("w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=list(CompanyResult.__dataclass_fields__.keys()))
                writer.writeheader()
                for result in matched:
                    writer.writerow(result.to_dict())
            return
        with output.open("w", encoding="utf-8") as file:
            for result in matched:
                file.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    @staticmethod
    def _visible(locator: Locator) -> bool:
        try:
            return locator.count() > 0 and locator.first.is_visible(timeout=1000)
        except TimeoutError:
            return False
