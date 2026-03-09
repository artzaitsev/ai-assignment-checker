## Корпус normalization fixtures

Эта директория - committed synthetic-only acceptance baseline для normalization.

Политика:
- Коммитим только synthetic fixture content или сильный paraphrase.
- Не копируем verbatim фрагменты из hidden sources в committed fixtures.
- Committed tests должны читать данные только из `tests/data/normalization/`.
- Не используем `data_hidden/` в committed unit/integration assertions.

Структура:
- `assignments/`: synthetic assignment definitions, которые используют case fixtures.
- `cases/`: per-case input, `meta.json`, expected output/error и при необходимости OCR stubs.
- `parser_io/`: parser contract fixtures (input/output + malformed outputs для repair tests).

Назначение binary fixtures:
- Office и PDF inputs - это реальные binary documents, а не plain text файлы с переименованным расширением.
- OCR-oriented cases включают embedded image content внутри source documents и детерминированный `ocr_stub.json`.
- Часть fixtures специально содержит copied prompts, SQL snippets, side notes и screenshot-like text, чтобы лучше приближаться к messy real submissions.

Local-only hidden corpus smoke:
1. Hidden inputs из `data_hidden/answers_real/` держим локально и не коммитим.
2. Local smoke запускаем через ad-hoc script или shell поверх hidden files.
3. Результаты local smoke храним вне committed test paths.
4. CI никогда не должен зависеть от доступности hidden data.

Связь cases с downstream acceptance tasks:
- Plain text и markdown-like extraction: `case_001` - `case_005`
- Office-document extraction: `case_006` - `case_008`
- PDF native extraction: `case_009`
- OCR routing: `case_007`, `case_010`, `case_011`
- Error mapping: `case_012`, `case_013`
- Parser schema и repair: `parser_io/*`
