# Сравнение после SQL-параметризации — контракт, не выполненный прогон

Настоящий исходник: `RepairLookupController.kt:12–14` (`ev-sql-before`) принимает frameTag и вставляет его в SQL. В том же файле :16–18 (`ev-sql-safe-control`) другой endpoint передаёт ticketCode отдельным аргументом к `?`. Второй метод — **существующий безопасный контроль для интерполяции**, не исправленный первый метод и не after run. Owner authorization этого контроля здесь не оценивалась.

Предлагаемая ручная правка учебной копии первого метода:

```text
SELECT ticket_code FROM repair_ticket WHERE frame_tag = ?
JdbcTemplate argument: frameTag
```

Это иллюстрация изменения одного sink, не сохранённый after-source. Original snapshot не меняем, Kotlin/Gradle не выполняем. B получает новую учебную копию, A фиксирует snapshot/run, B сканирует статически. Лишь после настоящих результатов C вычисляет comparison. Сейчас `afterSourceExists=false`, `afterRunExists=false`; ни `resolved`, ни измеренное улучшение не заявлены.

## Anchor и fingerprint

Уникальный Finding ID живёт внутри run. Сопоставление использует две сущности:

1. **Stable anchor кандидата:** language/framework, rule family, enclosing symbol (`RepairLookupController.findByFrameTag`), HTTP route `/api/customer/search/frame`, роль sink `JdbcTemplate.queryForList` + SQL target/parameter role. Не номер строки, literal input или весь hash файла. Перенос строк сохраняет anchor; rename требует явного matching B либо inconclusive.
2. **Fingerprint паттерна:** versioned SHA-256 от canonical tuple `ruleId/family + stable anchor + normalized context + basis version`. Контекст нормализует только безопасные для семантики trivia, имена локальных параметров по ролям и source coordinates. Он сохраняет отличие interpolation от parameter binding, доверенный/недоверенный input и authorization predicates. Нельзя нормализацией стереть саму проблему.

Устойчивость не означает неизменность fingerprint после смысловой правки: исправленный sink сопоставляется по anchor, а rule outcome проверяется повторно. Отдельно хранятся before/after fingerprints, evidence refs и причины решения. Хеши разных исходников закономерно различаются. Полный file hash нужен для provenance, не для единственного matching key.

Несколько одинаковых sinks в одном символе различаем structural role/нормализованным окружением. Несколько равноценных кандидатов → `inconclusive: ambiguous_anchor`, без выбора «первого похожего». Similarity/LLM не доказывают тождество. Fingerprint в pickup fixture — явно `illustration-sha256/1`, не согласованный алгоритм B.

Для SECRET value identity — HMAC со стабильным секретным ключом вне DTO/БД. В comparison сохраняется только `hmacKeyVersion`, не ключ/значение или его literal-части. После ротации разные HMAC не доказывают появление/исчезновение секрета; нужен согласованный перескан под одним ключом либо `inconclusive`.

## Сопоставимость прежде статуса

`ComparisonDto` должен хранить workspace, baseline/current run + snapshot IDs, обе AnalysisSignature, coverage затронутых file/rule/scope, matched anchors, reasons, items и отдельный `deletedFiles`. Оба run должны быть видимы вызывающему пользователю и зафиксированы; partial publish не становится полной проверкой.

По умолчанию требуем одинаковые parser version/hash, ruleset и rule version/hash, advisory snapshot version/hash, scan config hash (включая budgets/exclusions), domain policy version/hash и HMAC secret version. Плюс совместимую версию extraction/normalization/fingerprint. Запрошенные families/rules и scope должны соответствовать. AI/provider version хранится отдельно: смена объясняющей модели не меняет static facts.

В будущем допустима **явная согласованная compatibility mapping**, например advisory input не влияет на этот SAST rule. Без неё любое отличие signature → `inconclusive: incompatible_analysis`, а не тихое сравнение. Если policy была unknown/отсутствовала, одинаковые null не делают policy-dependent вывод сопоставимым; B должен явно указать `not_applicable` или недостаток оснований. Нельзя объявлять fixture с null versions сопоставимой с настоящим scanner run.

Проверяем **локальное покрытие**: файл успешно разобран, нужное правило действительно применялось, анализ достиг нужного sink, не было skip/error/unresolved dependency/budget limit, влияющих на вывод. `run=succeeded`, пустой finding list и общий счётчик filesVisited этого не доказывают. У параметризованного sink должен быть сохранён inspected/rule-not-matched outcome или эквивалентное scoped coverage. При недостающем outcome нет доказанного disappearance.

## Статусы для пользователя

| Статус | Условия и смысл |
| --- | --- |
| `persistent` — осталось | Однозначный anchor и тот же паттерн при совместимом анализе; перенос строк сам по себе ничего не исправил |
| `introduced` — появилось | Current finding не имеет baseline match, при этом baseline действительно покрывал соответствующий scope/rule; новый файл отдельно помечен `new_scope` |
| `resolved` — исчезло при сопоставимой проверке | Before finding был; after anchor/sink найден и проверен совместимым rule, этот паттерн больше не совпадает; это статическое исчезновение, не runtime-гарантия |
| `changed` — изменилось | Anchor однозначен, найдено поддержанное смысловое изменение основания/паттерна; сохраняем before/after evidence, не называем улучшением автоматически |
| `inconclusive` — сравнить нельзя | Signature/coverage/identity/anchor недостаточны, анализ не завершён или source недоступен; обязательна конкретная причина |

`file_deleted` — **отдельная секция** с before path/hash и подтверждением отсутствия в полном current snapshot manifest. Не Finding `resolved` и не число исправлений. Если current manifest частичный, нельзя даже уверенно объявить удаление. Исчезнувший под старым путём файл может быть rename; без подтверждённого matching остаётся неоднозначность. Новый файл без эквивалентного baseline scope не выдаём за доказанную регрессию.

## Ожидаемые сценарии будущей живой проверки

| Вход/действие | Ожидание, ещё не измеренное |
| --- | --- |
| Только перенос строк вокруг frame-tag sink | `persistent`; меняются locations/file hashes, anchor сохраняется |
| Связывание frameTag отдельным аргументом `?`, полный совместимый повторный scan | `resolved` для interpolation rule при inspected after sink; ticket owner policy не считается исправленной |
| Параметризация только части составного SQL, tainted fragment остался | `persistent` либо `changed` по фактам B; наличие одного `?` не доказывает fix |
| Файл удалён из полного snapshot | `deletedFiles: file_deleted`, не resolved |
| Файл пропущен, parse error, timeout или budget limit | `inconclusive` с file/rule coverage reason, не fix |
| Rule/policy/parser/config/advisory/HMAC version сменились | `inconclusive` либо перескан baseline/current под согласованной signature |
| Два одинаковых candidates после rename | `inconclusive: ambiguous_anchor`, до явного matching |
| Baseline этот scope не анализировал, current нашёл finding | `inconclusive: baseline_not_covered`; не ложное «появилось» |

Comparison не переписывает findings старых runs и не обновляет baseline автоматически. Если matching ещё не реализован, показываем два отдельных run с причиной `comparison_unavailable`. Эксплуатации и запуска приложения для этого статического proof loop не требуется; метаданные организатора и manual fixture не подставляются вместо собственных scan outputs.
