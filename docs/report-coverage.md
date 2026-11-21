# Report coverage completeness

Дата: 2026-11-21. Ветка `task/postgres-runtime`, local dev `85acb5a`.

## Проблема

`reports/builder.py` считал `completeness_score` только по parser errors и
unresolved dependencies. Для clean run это давало `1.0` / `100%`, хотя
canonical coverage был `partial` и пропускал `runtime-configuration`,
`runtime-guards`, `binaries-and-archives`. Markdown писал «Индекс полноты
проверки 100%», экран при этом честно показывал INCONCLUSIVE/Неполное.

Parser ratio — это не полнота security проверки. Его нельзя выдавать за общий
показатель.

## Правило

- Полнота новых отчётов берётся из canonical coverage и expected scopes.
- `completeness_known=true` и `completeness_score=1.0` возможны только при
  `coverageStatus=complete` и `expectedScopesVerified=true`.
- При `partial`, `unavailable`, отсутствии canonical данных или непроверенных
  expected scopes:
  - `completeness_known=false`;
  - `completeness_score=0.0` (fail closed, не «ноль просканированных файлов»);
  - `coverage_status` и `skipped_scopes` заполняются фактами;
  - `coverage_reasons` содержит русскую причину;
  - Markdown пишет «Полнота проверки: не установлена/недоступна/неизвестна»
    по статусу и причину, без 100%.
- Parser ratio остаётся отдельным legacy-полем `parser_completeness_score`;
  оно не используется как общая полнота и не выводится в Markdown.
- Если canonical данных нет, это честное `unknown`, а не фабрикованный
  `complete`.

## Совместимость JSON

Старое числовое поле `completeness_score` сохранено, но его смысл теперь —
canonical completeness ratio: `1.0` только для подтверждённого complete,
иначе `0.0`. Для отличия «неизвестно» от реального нуля добавлены
`completeness_known`, `coverage_status`, `coverage_reasons`, `skipped_scopes`.
Старый parser ratio сохранён как `parser_completeness_score`. Старые
сохранённые отчёты не переписываются.

Изменены `backend/app/reports/builder.py`, `backend/app/engine/executor.py`,
`backend/app/contracts/schemas.py` и этот файл. Frontend, router, storage и
redaction не менялись. На dev85 `ReportView` уже получает canonical coverage
из node runs через `frontend/src/coverage.ts`; `completeness_score` встречается
во frontend только в типе и не используется для отображения. Поэтому этот
backend followup не утверждает, что экран показывает 0.

## Живая проверка

Temp PostgreSQL/runtime, default workflow:

| Run | execution | security verdict | canonical coverage | completeness |
|---|---|---|---|---|
| clean | succeeded | inconclusive | partial, skipped 3 scopes | `known=false`, score `0.0`, Markdown «не установлена» |
| known findings (13) | succeeded | fail | partial, skipped 3 scopes | `known=false`, score `0.0`, Markdown «не установлена» |

Clean passport:

```text
completeness_score=0.0, completeness_known=false,
coverage_status=partial,
skipped_scopes=[runtime-configuration, runtime-guards, binaries-and-archives],
parser_completeness_score=1.0,
coverage_reasons=[Полнота проверки не установлена: проверена только часть
  областей; не проверено: конфигурация времени выполнения, защита во время
  выполнения, бинарники и архивы.]
```

Скачанный `/report/readable.md` содержит «Полнота проверки | **не установлена**»,
причину и не содержит `100%`. Known-findings отчёт сохраняет `verdict=fail`,
но полнота также честно не установлена; findings/verdict не подменяются.

Одноразовая render-проверка:

- `partial` + skipped IDs → «не установлена», русские названия трёх областей,
  машинные `skipped_scopes` не изменены;
- `unavailable` → «недоступна», без утверждения «canonical отсутствует»;
- `unknown` → «неизвестна», «данные покрытия не предоставлены»;
- `complete` + expected verified → «Полнота проверки | 100%»;
- `complete` без expected → «не установлена», «обязательные области проверки не
  подтверждены»;
- policy `fail` сохраняет `verdict=fail` независимо от полноты.

Ограничение: пока canonical coverage не покрывает runtime-слои и expected
scopes не верифицированы, полная полнота не может быть заявлена. Это
намеренно; после реального закрытия scopes builder выдаст 100% только при
подтверждённом complete.