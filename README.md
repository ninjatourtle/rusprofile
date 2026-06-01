# rusprofile parser

Playwright-парсер для расширенного поиска `rusprofile.ru`.

## Что делает

Скрипт открывает `https://www.rusprofile.ru/search-advanced` через HTTP-прокси, подгружает cookies и выставляет фильтры:

- статус: только **Действующая**;
- вид деятельности: ОКВЭД `46.9`;
- регион: `Москва`;
- количество сотрудников: от `5`.

После этого он проходит все страницы выдачи, открывает карточку каждой компании и сохраняет только компании, у которых:

- отсутствует сайт или почта;
- дата регистрации совпадает с датой назначения руководителя;
- выручка положительная;
- прибыль положительная.

> Важно: селекторы сделаны устойчивыми к небольшим изменениям верстки и ищут элементы по русским текстам, ролям и ссылкам карточек. Если rusprofile изменит интерфейс или включит дополнительную антибот-проверку, может понадобиться корректировка конкретных селекторов.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
```

## Cookies

По умолчанию парсер ищет `cookies.json`. Поддерживаются:

- Playwright `storage_state.json`;
- JSON-массив cookies, экспортированный расширением браузера;
- Netscape `cookies.txt`.

Можно указать другой путь:

```bash
rusprofile-parser --cookies data/cookies.json
```

## Запуск

Самый простой запуск использует прокси, `cookies.json` и `output/rusprofile_results.jsonl` по умолчанию:

```bash
rusprofile-parser
```

То же самое можно запустить без console-script, через модуль Python:

```bash
python -m rusprofile_parser
```

Парсер пишет в консоль текущий шаг работы и сохраняет каждую подходящую компанию сразу после проверки карточки, а не только в конце обхода.

Если нужно явно указать cookies или файл результата:

```bash
rusprofile-parser --cookies cookies.json --output output/rusprofile_results.jsonl
```

Переопределение прокси:

```bash
rusprofile-parser --proxy 'host:port:user:password'
```

Отладочный запуск с браузером:

```bash
rusprofile-parser --headed --slow-mo 200 --limit 5
```

CSV-выгрузка:

```bash
rusprofile-parser --output output/rusprofile_results.csv
```

Если нужно считать подходящими только компании, у которых нет одновременно и сайта, и почты:

```bash
rusprofile-parser --require-both-missing-contacts
```
