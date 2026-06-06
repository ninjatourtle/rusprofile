from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError, sync_playwright

from .models import CompanyCandidate, CompanyResult
from .output import append_result, prepare_output, write_results
from .utils import (
    _is_ignored_website,
    check_domain_registration,
    extract_domain_from_website,
    extract_email,
    extract_website,
    first_date_after,
    load_cookies,
    parse_money_after,
    parse_proxy,
)

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
        self._domain_status_cache: dict[str, str | None] = {}

    def run(self, output: Path, limit: int | None = None, all_output: Path | None = None) -> list[CompanyResult]:
        output.parent.mkdir(parents=True, exist_ok=True)
        prepare_output(output)
        if all_output:
            all_output.parent.mkdir(parents=True, exist_ok=True)
            prepare_output(all_output)
        results: list[CompanyResult] = []
        self._log("Запускаю браузер")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo_ms,
                proxy=parse_proxy(self.proxy),
            )
            context_options = {
                "locale": "ru-RU",
                "timezone_id": "Europe/Moscow",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
                ),
            }
            storage_state = self._storage_state_path()
            if storage_state:
                context_options["storage_state"] = str(storage_state)
            context = browser.new_context(**context_options)
            page: Page | None = None
            try:
                context.set_default_timeout(self.timeout_ms)
                if storage_state:
                    self._log(f"Загрузил storage_state: {storage_state}")
                else:
                    self._load_context_cookies(context)
                page = context.new_page()
                self._apply_filters(page)
                for candidate in self._iter_all_candidates(page, limit):
                    self._log(f"Проверяю компанию: {candidate.name} ({candidate.url})")
                    try:
                        result = self._inspect_company(context, candidate)
                    except Exception as exc:
                        self._log(
                            "Ошибка при проверке карточки, пропускаю и продолжаю: "
                            f"{candidate.name}: {type(exc).__name__}: {exc}"
                        )
                        continue
                    results.append(result)
                    if all_output:
                        append_result(all_output, result)
                    if result.matched:
                        append_result(output, result)
                        self._log_success(f"Подходит, сразу сохранил: {result.name}")
                    else:
                        self._log_dim(f"Не подходит: {result.name}")
            except Exception as exc:
                self._log(f"Критическая ошибка: {type(exc).__name__}: {exc}")
                if page:
                    self._save_debug_page(page, "fatal_error")
                if not self.headless:
                    self._log("Браузер оставлен открытым. Посмотрите страницу и нажмите Enter в консоли, чтобы закрыть.")
                    try:
                        input()
                    except EOFError:
                        pass
                raise
            finally:
                self._save_context_cookies(context)
                context.close()
                browser.close()
        matched = sum(1 for result in results if result.matched)
        self._log(f"Готово. Проверено: {len(results)}, подходит: {matched}, файл: {output}")
        if all_output:
            self._log(f"CSV со всеми проверенными компаниями: {all_output}")
        return results

    def _storage_state_path(self) -> Path | None:
        if not self.cookies or not self.cookies.exists() or self.cookies.suffix.lower() != ".json":
            return None
        try:
            data = json.loads(self.cookies.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict) and isinstance(data.get("origins"), list):
            return self.cookies
        return None

    def _load_context_cookies(self, context: BrowserContext) -> None:
        if not self.cookies or not self.cookies.exists():
            self._log("Cookies не найдены, продолжаю без них")
            return
        data = load_cookies(self.cookies)
        cookies = data.get("cookies", []) if isinstance(data, dict) else data
        if not cookies:
            self._log(f"Cookies файл пустой: {self.cookies}")
            return
        bootstrap = context.new_page()
        try:
            bootstrap.goto(BASE_URL, wait_until="domcontentloaded")
            context.add_cookies(cookies)
        finally:
            bootstrap.close()
        self._log(f"Загрузил cookies: {len(cookies)} шт. из {self.cookies}")

    def _save_context_cookies(self, context: BrowserContext) -> None:
        if not self.cookies or self.cookies.suffix.lower() != ".json":
            return
        try:
            context.storage_state(path=self.cookies)
            self._log(f"Сохранил актуальные cookies/storage_state: {self.cookies}")
        except Exception as exc:
            self._log(f"Не удалось сохранить cookies: {type(exc).__name__}: {exc}")

    def _apply_filters(self, page: Page) -> None:
        self._log(f"Открываю расширенный поиск: {SEARCH_URL}")
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        self._ensure_search_page_available(page)
        self._dismiss_overlays(page)
        try:
            self._log("Выставляю статус: только действующие")
            self._set_status_active_only(page)
            self._log("Выбираю ОКВЭД: 46.9")
            self._select_activity(page, "46.9")
            self._log("Выбираю регион: Москва")
            self._select_region(page, "Москва")
            self._log("Выставляю сотрудников: от 5")
            self._set_employee_min(page, "5")
            self._log("Применяю фильтры")
            self._submit_filters(page)
        except Exception as exc:
            if self.headless:
                raise
            self._log(f"Не смог автоматически выставить фильтры: {type(exc).__name__}: {exc}")
            self._save_debug_page(page, "filter_error")
            self._wait_for_manual_filter_continue(page)
            self._log("Продолжаю после ручной настройки фильтров")

    def _ensure_search_page_available(self, page: Page) -> None:
        text = page.locator("body").inner_text(timeout=5000)
        if "Активность с вашего IP-адреса была распознана как автоматическая" not in text:
            return
        screenshot, _html = self._save_debug_page(page, "blocked_search")
        raise RuntimeError(
            "Rusprofile показал антибот-проверку вместо формы поиска. "
            "Запустите run.bat --headed, пройдите 'Я не робот' в открытом браузере и повторите запуск. "
            f"Скриншот сохранен: {screenshot}"
        )

    def _save_debug_page(self, page: Page, name: str) -> tuple[Path, Path]:
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot = output_dir / f"{name}.png"
        html = output_dir / f"{name}.html"
        page.screenshot(path=screenshot, full_page=True)
        html.write_text(page.content(), encoding="utf-8")
        self._log(f"Сохранил диагностику страницы: {screenshot}, {html}")
        return screenshot, html

    def _wait_for_manual_filter_continue(self, page: Page) -> None:
        self._log("Выставьте фильтры вручную в браузере, затем нажмите Ctrl+Enter на странице.")
        page.evaluate(
            """() => {
                window.__rusprofileManualContinue = false;
                const existing = document.getElementById('rusprofile-parser-manual-continue');
                if (existing) existing.remove();
                const notice = document.createElement('div');
                notice.id = 'rusprofile-parser-manual-continue';
                notice.textContent = 'Парсер ждет ручную настройку фильтров. Нажмите Ctrl+Enter, чтобы продолжить сбор.';
                Object.assign(notice.style, {
                    position: 'fixed',
                    zIndex: '2147483647',
                    left: '16px',
                    bottom: '16px',
                    maxWidth: '520px',
                    padding: '12px 14px',
                    background: '#111827',
                    color: '#fff',
                    font: '14px Arial, sans-serif',
                    borderRadius: '6px',
                    boxShadow: '0 8px 24px rgba(0,0,0,.25)'
                });
                document.body.appendChild(notice);
                window.__rusprofileManualContinueHandler = event => {
                    if (event.ctrlKey && event.key === 'Enter') {
                        window.__rusprofileManualContinue = true;
                        notice.textContent = 'Продолжаю сбор...';
                    }
                };
                window.addEventListener('keydown', window.__rusprofileManualContinueHandler);
            }"""
        )
        while True:
            if page.evaluate("() => Boolean(window.__rusprofileManualContinue)"):
                break
            page.wait_for_timeout(500)
        page.evaluate(
            """() => {
                const notice = document.getElementById('rusprofile-parser-manual-continue');
                if (notice) notice.remove();
                if (window.__rusprofileManualContinueHandler) {
                    window.removeEventListener('keydown', window.__rusprofileManualContinueHandler);
                }
            }"""
        )

    def _dismiss_overlays(self, page: Page) -> None:
        for text in ("Принять", "Понятно", "Согласен", "Закрыть"):
            button = page.get_by_text(text, exact=True)
            if self._visible(button):
                self._log(f"Закрываю всплывающее окно: {text}")
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
        self._select_tree_code(page, "okved", okved)

    def _select_region(self, page: Page, region: str) -> None:
        fieldset = self._open_tree_modal(page, "region")
        tree = fieldset.locator('ul.tree-list[data-name="region"]')
        search = fieldset.locator("input.filter-field")
        if tree.count() == 0:
            tree = page.locator('ul.tree-list[data-name="region"]')
        if search.count() == 0:
            search = page.locator("input.filter-field:visible").last
        search.scroll_into_view_if_needed()
        search.click()
        search.fill("")
        search.press_sequentially(region, delay=50)
        page.wait_for_timeout(1000)
        option = tree.locator("label, span.name", has_text=re.compile(re.escape(region), re.IGNORECASE)).last
        option.scroll_into_view_if_needed()
        option.click(force=True)
        page.wait_for_timeout(300)
        self._click_tree_done(page, tree)

    def _select_tree_code(self, page: Page, tree_name: str, code: str) -> None:
        fieldset = self._open_tree_modal(page, tree_name)
        tree = fieldset.locator(f'ul.tree-list[data-name="{tree_name}"]')
        search = fieldset.locator("input.filter-field")
        if tree.count() == 0:
            tree = page.locator(f'ul.tree-list[data-name="{tree_name}"]')
        if search.count() == 0:
            search = page.locator("input.filter-field:visible").last
        search.wait_for(state="visible", timeout=self.timeout_ms)
        search.scroll_into_view_if_needed()
        search.click()
        search.fill("")
        search.press_sequentially(code, delay=50)
        page.wait_for_timeout(1000)
        try:
            self._pick_tree_code(page, tree, code)
        except RuntimeError:
            fallback_code = code.split(".", 1)[0]
            if fallback_code == code:
                raise
            self._log(f"Не нашёл ОКВЭД {code}, пробую родительский код {fallback_code}")
            search.fill("")
            search.press_sequentially(fallback_code, delay=50)
            page.wait_for_timeout(1000)
            self._pick_tree_code(page, tree, fallback_code)
        self._click_tree_done(page, tree)

    def _pick_tree_code(self, page: Page, tree: Locator, code: str) -> None:
        option = tree.locator(f'[data-code="{code}"]')
        if option.count() == 0:
            self._expand_tree_parents(page, tree, code)
        option = tree.locator(f'[data-code="{code}"]')
        if option.count() == 0:
            escaped = re.escape(code)
            option = tree.locator("span.code", has_text=re.compile(rf"^{escaped}\.?$"))
        if option.count() == 0:
            raise RuntimeError(f"Не удалось выбрать значение {code!r} в дереве фильтра")
        row = option.first.locator("xpath=ancestor::*[@data-code][1]")
        target = row if row.count() else option.first
        try:
            target.scroll_into_view_if_needed(timeout=5000)
        except TimeoutError:
            pass
        label = target.locator("label")
        if label.count():
            self._dom_click(label.first)
        else:
            self._dom_click(target)
        page.wait_for_timeout(300)

    def _expand_tree_parents(self, page: Page, tree: Locator, code: str) -> None:
        parts = code.split(".")
        for index in range(1, len(parts)):
            parent = ".".join(parts[:index])
            if tree.locator(f'[data-code="{code}"]').count():
                return
            node = tree.locator(f'[data-code="{parent}"]')
            if node.count() == 0:
                continue
            node.first.scroll_into_view_if_needed()
            sublist = node.locator("xpath=following-sibling::ul[contains(@class, 'sublist')]")
            if sublist.count() and sublist.first.is_visible():
                continue
            node.first.locator("span.code").click(force=True)
            page.wait_for_timeout(500)
            if tree.locator(f'[data-code="{code}"]').count():
                return
            node.first.evaluate(
                """element => {
                element.classList.add('expanded');
                element.dispatchEvent(new Event('click', { bubbles: true }));
            }"""
            )
            page.wait_for_timeout(500)

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
        if target.count() == 0:
            raise RuntimeError(f"Не найден фильтр: {name}")
        target.click(timeout=5000)
        page.wait_for_timeout(500)

    def _tree_fieldset(self, page: Page, tree_name: str) -> Locator:
        return page.locator(
            f'xpath=//ul[contains(concat(" ", normalize-space(@class), " "), " tree-list ") '
            f'and @data-name="{tree_name}"]/ancestor::fieldset[1]'
        )

    def _open_tree_modal(self, page: Page, tree_name: str) -> Locator:
        filter_names = {
            "okved": ("Вид деятельности", "ОКВЭД", "Основной вид деятельности"),
            "region": ("Регион",),
            "okopf": ("ОКОПФ",),
        }
        fieldset = self._tree_fieldset(page, tree_name)
        search = fieldset.locator("input.filter-field")
        if self._visible(search):
            return fieldset

        for filter_name in filter_names.get(tree_name, ()):
            try:
                self._open_tree_toggle(page, filter_name)
                page.wait_for_timeout(500)
                fieldset = self._tree_fieldset(page, tree_name)
                search = fieldset.locator("input.filter-field")
                if self._visible(search):
                    return fieldset
                search = page.locator(f'ul.tree-list[data-name="{tree_name}"]').locator(
                    "xpath=ancestor::div[contains(@class, 'modal-pop-body')][1]"
                ).locator("input.filter-field")
                if self._visible(search):
                    return self._visible_tree_container(page, tree_name)
            except (RuntimeError, TimeoutError):
                self._log(f"Не удалось открыть фильтр по названию: {filter_name}")

        if page.locator(f'ul.tree-list[data-name="{tree_name}"]').count() > 0:
            self._log(f"Дерево {tree_name} есть в DOM, продолжаю без открытия модалки")
            return self._visible_tree_container(page, tree_name)

        toggle = fieldset.locator(".toggle-fields.has-list-tree")
        if not self._visible(toggle):
            toggle = fieldset.locator(".toggle-fields")
        if toggle.count() == 0:
            legends = self._available_tree_filter_legends(page)
            raise RuntimeError(f"Не найден переключатель дерева фильтра: {tree_name}. Доступные деревья: {legends}")
        try:
            self._dom_click(toggle.first)
        except TimeoutError as exc:
            raise RuntimeError(f"Не удалось открыть дерево фильтра: {tree_name}") from exc
        search.wait_for(state="visible", timeout=self.timeout_ms)
        page.wait_for_timeout(300)
        return fieldset

    def _open_tree_toggle(self, page: Page, name: str) -> None:
        escaped = self._xpath_literal(name)
        toggle = page.locator(
            "xpath=//legend[contains(normalize-space(.), "
            f"{escaped})]/ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' toggle-fields ')][1]"
        ).first
        if toggle.count() == 0:
            self._open_filter(page, name)
            return
        try:
            toggle.scroll_into_view_if_needed(timeout=5000)
        except TimeoutError:
            pass
        self._dom_click(toggle)

    def _visible_tree_container(self, page: Page, tree_name: str) -> Locator:
        tree = page.locator(f'ul.tree-list[data-name="{tree_name}"]')
        modal = tree.locator("xpath=ancestor::div[contains(@class, 'modal-pop-body')]")
        if modal.count():
            return modal.last
        fieldset = self._tree_fieldset(page, tree_name)
        if fieldset.count():
            return fieldset.last
        return page.locator("body")

    def _available_tree_filter_legends(self, page: Page) -> list[str]:
        legends: list[str] = []
        toggles = page.locator(".toggle-fields.has-list-tree legend")
        for index in range(toggles.count()):
            text = toggles.nth(index).inner_text(timeout=1000).strip()
            if text:
                legends.append(text)
        return legends

    def _dom_click(self, locator: Locator) -> None:
        locator.evaluate(
            """element => {
                element.click();
                element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
            }"""
        )

    @staticmethod
    def _xpath_literal(value: str) -> str:
        if '"' not in value:
            return f'"{value}"'
        if "'" not in value:
            return f"'{value}'"
        parts = value.split('"')
        return "concat(" + ', \'"\', '.join(f'"{part}"' for part in parts) + ")"

    def _tree_list(self, page: Page, tree_name: str) -> Locator:
        return page.locator(f'ul.tree-list[data-name="{tree_name}"]')

    def _click_tree_done(self, page: Page, tree: Locator) -> None:
        modal = tree.locator("xpath=ancestor::div[contains(@class, 'modal-pop-body')]")
        done = modal.locator(".tree-list-submit") if modal.count() else tree.locator(".tree-list-submit")
        done.scroll_into_view_if_needed()
        done.click(force=True)
        page.wait_for_timeout(500)

    def _find_active_input(self, page: Page, preferred_placeholder: str | None = None) -> Locator:
        for tree_name in ("okved", "region", "okopf"):
            tree_search = page.locator(f'ul.tree-list[data-name="{tree_name}"] input.filter-field:visible')
            if self._visible(tree_search):
                return tree_search.first
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

    def _iter_all_candidates(self, page: Page, limit: int | None) -> Iterable[CompanyCandidate]:
        seen: set[str] = set()
        collected = 0
        page_number = 1
        while True:
            self._log(f"Считываю страницу выдачи №{page_number}")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
            self._scroll_results_page(page)
            page_candidates = 0
            for candidate in list(self._extract_candidates(page)):
                if candidate.url in seen:
                    continue
                seen.add(candidate.url)
                collected += 1
                page_candidates += 1
                self._log(f"Нашёл карточку #{collected}: {candidate.name}")
                yield candidate
                if limit and collected >= limit:
                    self._log(f"Достигнут лимит: {limit}")
                    return
            self._log(f"На странице №{page_number} новых карточек: {page_candidates}")
            if not self._go_next_page(page):
                break
            page_number += 1

    def _collect_all_candidates(self, page: Page, limit: int | None) -> list[CompanyCandidate]:
        return list(self._iter_all_candidates(page, limit))

    def _extract_candidates(self, page: Page) -> Iterable[CompanyCandidate]:
        links = page.locator('a[href^="/id/"], a[href*="/id/"]')
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href")
            if not href:
                continue
            url = href if href.startswith("http") else f"{BASE_URL}{href}"
            text = link.inner_text().strip()
            card_text = link.locator(
                "xpath=ancestor::*[self::div or self::article or self::li][1]"
            ).inner_text(timeout=3000)
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
        before_url = page.url
        before_first_url = self._first_candidate_url(page)
        before_links = page.locator('a[href^="/id/"], a[href*="/id/"]').count()
        before_active_page = self._active_page_number(page)
        next_control = self._next_paging_control(page, before_active_page)
        if next_control is not None:
            target_page = next_control.get_attribute("data-target")
            self._log(f"Перехожу к следующей странице выдачи{f' №{target_page}' if target_page else ''}")
            self._dom_click(next_control)
            if self._wait_for_page_change(page, before_url, before_first_url, before_active_page, before_links):
                return True
        for selector in (
            'button:has-text("Показать еще")',
            'button:has-text("Показать ещё")',
            'a:has-text("Показать еще")',
            'a:has-text("Показать ещё")',
            'a[rel="next"]',
            'a:has-text("Следующая")',
            'a:has-text("Далее")',
            'button:has-text("Далее")',
            'button:has-text("Показать все")',
            '.pagination a.next',
            '.paging a.next',
        ):
            control = page.locator(selector)
            if control.count() > 0 and (self._visible(control) or "fakelink" in (control.first.get_attribute("class") or "")):
                target_page = control.first.get_attribute("data-target")
                self._log(f"Перехожу к следующей странице выдачи{f' №{target_page}' if target_page else ''}")
                self._dom_click(control.first)
                if self._wait_for_page_change(page, before_url, before_first_url, before_active_page, before_links):
                    return True
        if before_active_page and self._goto_page_by_target(page, before_active_page + 1):
            self._log(f"Перехожу к следующей странице выдачи №{before_active_page + 1}")
            if self._wait_for_page_change(page, before_url, before_first_url, before_active_page, before_links):
                return True
        self._log(f"Следующей страницы нет. Активная страница: {before_active_page or '-'}, пагинация: {self._paging_targets(page)}")
        return False

    def _next_paging_control(self, page: Page, active_page: int | None) -> Locator | None:
        controls = page.locator(
            ".paging-list .nav-next[data-target], .paging .nav-next[data-target], "
            ".paging-list .to-page[data-target], .paging .to-page[data-target]"
        )
        fallback: Locator | None = None
        for index in range(controls.count()):
            control = controls.nth(index)
            classes = control.get_attribute("class") or ""
            target = control.get_attribute("data-target")
            if "disabled" in classes or not target:
                continue
            try:
                target_number = int(target)
            except ValueError:
                continue
            if active_page and target_number <= active_page:
                continue
            if "nav-next" in classes:
                return control
            if fallback is None:
                fallback = control
        return fallback

    def _wait_for_page_change(
        self,
        page: Page,
        before_url: str,
        before_first_url: str | None,
        before_active_page: int | None,
        before_links: int,
    ) -> bool:
        for _ in range(12):
            page.wait_for_timeout(500)
            if page.url != before_url:
                page.wait_for_load_state("domcontentloaded")
                return True
            after_active_page = self._active_page_number(page)
            if before_active_page and after_active_page and after_active_page > before_active_page:
                return True
            after_first_url = self._first_candidate_url(page)
            if before_first_url and after_first_url and after_first_url != before_first_url:
                return True
            after_links = page.locator('a[href^="/id/"], a[href*="/id/"]').count()
            if after_links > before_links:
                return True
        self._scroll_results_page(page)
        after_first_url = self._first_candidate_url(page)
        return bool(before_first_url and after_first_url and after_first_url != before_first_url)

    def _goto_page_by_target(self, page: Page, target_page: int) -> bool:
        return bool(
            page.evaluate(
                """target => {
                    const selectors = [
                        `.paging-list [data-target="${target}"]`,
                        `.paging [data-target="${target}"]`,
                        `[data-target="${target}"].to-page`,
                        `[data-target="${target}"].nav-next`
                    ];
                    for (const selector of selectors) {
                        const element = document.querySelector(selector);
                        if (!element) continue;
                        element.click();
                        element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        return true;
                    }
                    return false;
                }""",
                target_page,
            )
        )

    def _paging_targets(self, page: Page) -> list[str]:
        values: list[str] = []
        controls = page.locator(".paging-list [data-target], .paging [data-target]")
        for index in range(controls.count()):
            target = controls.nth(index).get_attribute("data-target")
            classes = controls.nth(index).get_attribute("class") or ""
            if target:
                values.append(f"{target}:{classes}".strip())
        return values

    def _active_page_number(self, page: Page) -> int | None:
        active = page.locator(".paging-list li.active .fakelink, .paging li.active .fakelink")
        if active.count() == 0:
            return None
        text = active.first.inner_text(timeout=1000).strip()
        return int(text) if text.isdigit() else None

    def _first_candidate_url(self, page: Page) -> str | None:
        links = page.locator('a[href^="/id/"], a[href*="/id/"]')
        if links.count() == 0:
            return None
        href = links.first.get_attribute("href")
        if not href:
            return None
        return href if href.startswith("http") else f"{BASE_URL}{href}"

    def _scroll_results_page(self, page: Page) -> None:
        previous_height = page.evaluate("document.body.scrollHeight")
        for _ in range(3):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(700)
            current_height = page.evaluate("document.body.scrollHeight")
            if current_height == previous_height:
                break
            previous_height = current_height

    def _inspect_company(self, context: BrowserContext, candidate: CompanyCandidate) -> CompanyResult:
        page = context.new_page()
        try:
            page.goto(candidate.url, wait_until="domcontentloaded")
            page.wait_for_timeout(700)
            text = page.locator("body").inner_text()
            registration_date = first_date_after("Дата регистрации", text) or first_date_after("ОГРН", text)
            director_since = self._extract_director_since(page)
            has_ceo_history = self._has_ceo_history_badge(page)
            arbitr_cases_count = self._extract_arbitration_cases_count(page)
            revenue = parse_money_after("Выручка", text)
            profit = parse_money_after("Прибыль", text) or parse_money_after("Чистая прибыль", text)
            email = extract_email(text)
            website = self._extract_company_website(page) or extract_website(text)
            website_domain = extract_domain_from_website(website)
            website_domain_status = self._check_domain_status(website_domain)
            reasons: list[str] = []
            if not has_ceo_history:
                reasons.append("нет плашки истории руководителей")
            if arbitr_cases_count <= 5:
                reasons.append("арбитражных дел не больше 5")
            if revenue is not None and revenue > 0:
                reasons.append("выручка положительная")
            if profit is not None and profit > 0:
                reasons.append("прибыль положительная")
            contacts_missing = (
                not website and not email if self.require_both_missing_contacts else (not website or not email)
            )
            if contacts_missing:
                reasons.append("нет сайта или почты" if not self.require_both_missing_contacts else "нет сайта и почты")
            matched = bool(
                contacts_missing
                and not has_ceo_history
                and arbitr_cases_count <= 5
                and revenue is not None
                and revenue > 0
                and profit is not None
                and profit > 0
            )
            self._log(
                "Данные карточки: "
                f"регистрация={registration_date or '-'}, директор={director_since or '-'}, "
                f"история руководителей={'да' if has_ceo_history else 'нет'}, "
                f"арбитражных дел={arbitr_cases_count}, "
                f"выручка={revenue if revenue is not None else '-'}, "
                f"прибыль={profit if profit is not None else '-'}, "
                f"email={email or '-'}, сайт={website or '-'}, "
                f"домен={website_domain or '-'}, статус домена={website_domain_status or '-'}"
            )
            return CompanyResult(
                name=candidate.name,
                url=candidate.url,
                inn=candidate.inn,
                ogrn=candidate.ogrn,
                registration_date=registration_date,
                director_since=director_since,
                has_ceo_history=has_ceo_history,
                arbitr_cases_count=arbitr_cases_count,
                revenue=revenue,
                profit=profit,
                email=email,
                website=website,
                website_domain=website_domain,
                website_domain_status=website_domain_status,
                matched=matched,
                reasons=reasons,
            )
        finally:
            page.close()

    def _extract_director_since(self, page: Page) -> str | None:
        rows = page.locator(".company-row", has=page.locator(".company-info__title", has_text="Руководитель"))
        for index in range(rows.count()):
            row_text = rows.nth(index).inner_text(timeout=1000).replace("\xa0", " ")
            match = re.search(r"\bс\s+\d{1,2}\s+[А-Яа-яЁё]+\s+\d{4}\s*г\.?", row_text)
            if match:
                return re.sub(r"\s+", " ", match.group(0)).strip()
        text = page.locator("body").inner_text(timeout=1000).replace("\xa0", " ")
        match = re.search(r"Руководитель[\s\S]{0,500}?\b(с\s+\d{1,2}\s+[А-Яа-яЁё]+\s+\d{4}\s*г\.?)", text)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
        return None

    def _has_ceo_history_badge(self, page: Page) -> bool:
        selectors = (
            'a.has_history.quetip[href*="/history/"][href*="group=ceo"]',
            'a[data-goal*="history_ceo"][href*="/history/"]',
            'a[data-track-click*="history_company_ceo"]',
            'a[data-quetip*="предыдущим"][data-quetip*="руковод"]',
        )
        for selector in selectors:
            if page.locator(selector).count() > 0:
                return True
        return False

    def _extract_arbitration_cases_count(self, page: Page) -> int:
        tile = page.locator(".arbitr-tile")
        if tile.count() == 0:
            return 0
        selectors = (
            '.tab-items[data-tab_name="cases_all_now"] .tab-item.active a.num',
            '.tab-items[data-tab_name="cases_all_now"] .tab-item.active .num',
            'a.gtm_ar_1.num',
            '.connexion-col__num a.num',
        )
        for selector in selectors:
            values = tile.locator(selector)
            for index in range(values.count()):
                text = values.nth(index).inner_text(timeout=1000)
                match = re.search(r"\d+", text.replace("\xa0", " "))
                if match:
                    return int(match.group(0))
        return 0

    def _extract_company_website(self, page: Page) -> str | None:
        contact_site = page.locator(".company-info__contact.site a[itemprop='url'], .company-info__contact.site a")
        for index in range(contact_site.count()):
            link = contact_site.nth(index)
            href = (link.get_attribute("href") or "").strip()
            text = link.inner_text(timeout=1000).strip()
            value = text or href
            if not value:
                continue
            normalized = value.removeprefix("http://").removeprefix("https://").removeprefix("//").rstrip("/")
            if _is_ignored_website(normalized):
                continue
            if re.search(r"\b(?:www\.)?[\w-]+\.(?:ru|com|net|org|рф)\b", normalized, re.IGNORECASE):
                return normalized
        return None

    def _check_domain_status(self, domain: str | None) -> str | None:
        if not domain:
            return None
        if domain not in self._domain_status_cache:
            self._domain_status_cache[domain] = check_domain_registration(domain)
        return self._domain_status_cache[domain]

    def _prepare_output(self, output: Path) -> None:
        prepare_output(output)

    def _append_result(self, output: Path, result: CompanyResult) -> None:
        append_result(output, result)

    def _write_results(self, output: Path, results: list[CompanyResult]) -> None:
        write_results(output, results)

    @staticmethod
    def _visible(locator: Locator) -> bool:
        try:
            return locator.count() > 0 and locator.first.is_visible(timeout=1000)
        except TimeoutError:
            return False

    @staticmethod
    def _log(message: str) -> None:
        print(f"[rusprofile-parser] {message}", flush=True)

    @staticmethod
    def _log_success(message: str) -> None:
        print(f"\033[92m[rusprofile-parser] {message}\033[0m", flush=True)

    @staticmethod
    def _log_dim(message: str) -> None:
        print(f"\033[90m[rusprofile-parser] {message}\033[0m", flush=True)
