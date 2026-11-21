# Полный анализ по умолчанию

Один запуск проверяет каждый выбранный сканер двумя движками: собственными
анализаторами backend и Team B (`analyzers/cg26`). Это и есть полный анализ.
Отдельного «слабого» режима для новых запусков больше нет.

## Как выбирается анализ

- `config.engine` отсутствует, `full`, `auto`, `backend` или `team_b` — для всех
  этих значений выполняется полный анализ.
- Старые сохранённые revision с `engine: backend`, `engine: team_b` и даже со
  смешанными значениями продолжают валидироваться и исполняются как полный
  анализ. Исторические immutable revisions/результаты не переписываются.
- Набор категорий задаётся графом: есть `secret_scan` — выполняется SECRET,
  есть `sca_scan` — SCA, есть `kotlin_sast` — SAST. Если ноду категории убрали,
  она не включается скрыто.
- Неизвестное значение `engine` остаётся ошибкой валидации.

## Что происходит в ноде

`backend/app/integrations/full_analysis.py` запускает оба движка параллельно
для категории ноды:

| Категория | Backend | Team B |
| --- | --- | --- |
| SECRET | `SecretAnalyzer` | `CG26-SEC-*` |
| SCA | `ScaAnalyzer` | `CG26-SCA-001` |
| SAST | `SastAnalyzer` | `CG26-SAST-001..005` |

Затем результаты объединяются по идентичности уязвимости, а не по внутреннему
fingerprint движка:

- SECRET — один файл и пересекающиеся строки;
- SCA — один файл, пересекающиеся строки и общий advisory id (с алиасами из
  доверенного каталога);
- SAST — один файл, пересекающиеся строки и один класс правила.

Совпавшие находки сохраняют канонический backend rule id и получают trace и
related locations обоих движков. Backend-only классы (`KOTLIN-PATH-TRAVERSAL`,
`KOTLIN-COMMAND-INJECTION`) остаются, даже если Team B их не видит. Codefacts
находки проходят ту же проверку перед картой: `CF-SQL-PARAM-INTERPOLATION` и
`CF-ROUTE-UNAUTH-PII` не дублируют уже найденные SQL/route-находки, а
непроверенное `CF-OWNER-CHECK-UNPROVEN` остаётся отдельной inconclusive-записью.

## Ошибки и деградация

Один упавший движок не отменяет второй:

- исключение движка превращается в `Diagnostic(level="error")` своей категории;
- находки второго движка сохраняются;
- policy gate видит ошибку и не выдаёт чистый PASS: вердикт становится
  `inconclusive`, либо остаётся `FAIL`, но в reasons добавляется неполнота
  покрытия;
- отчёт и карта получают `coverage.status=partial` и причины, а не заявляют
  исчерпывающий анализ.

Также честно деградируют:

- отсутствующий/битый advisory snapshot — SCA пишет
  `sca.database-missing`, SECRET/SAST Team B продолжают работу;
- отсутствующий `CG26_HMAC_KEY`/`CG26_HMAC_KEY_VERSION` — Team B недоступен,
  backend-находки остаются, покрытие неполное;
- неразрешённые версии/зависимости и ошибки парсинга — диагностики и
  `partial`, а не молчаливый успех.

## Доверенный advisory snapshot

Основной файл: `analyzers/advisory-snapshot/trusted-advisories.json`
(схема `nedra-trusted-advisories/1`).

- источник: `TBagirov/training`, `vulnerability-database/vulnerabilities.json`;
- commit `fe1fcbe212669783480666b9b60147c8a8c6eef4`, blob
  `c34253459d1ae3f621d997534a513ab59e37f683`;
- лицензия upstream: GitHub Advisory Database, CC BY 4.0, через OSV;
- 41 organizer-запись + 4 сохранённые записи прежнего backend-каталога;
- у каждой записи есть `provenance`.

`backend/app/analyzers/advisories.json` — legacy-проекция того же снимка для
backend SCA и AI-валидатора фиксированных версий. `analyzers/fixtures/
vulnerabilities.json` остаётся минимальным self-test и не выдаётся за полный
каталог.

Загруженный ZIP не может выбрать или подменить каталог: Team B adapter
подменяет `locate_database` и возвращает только серверный путь; backend SCA
читает файл рядом с приложением.

## Переменные окружения

```text
CG26_HMAC_KEY              # обязателен для Team B, только runtime secret
CG26_HMAC_KEY_VERSION      # обязателен, не local-demo-1
CG26_ADVISORY_DB           # optional; локальный серверный файл
CG26_ADVISORY_ROOT         # optional; containment для CG26_ADVISORY_DB
CG_SESSION_SECRET          # уже требуется для canonical SECRET evidence
CG_ANCHOR_HMAC_KEY         # уже требуется для canonical SECRET evidence
```

`CG26_ADVISORY_DB` не принимает URL. Если путь не задан, используется встроенный
snapshot. Если заданный путь отсутствует/вне `CG26_ADVISORY_ROOT`, используется
встроенный snapshot с предупреждением. Секреты не пишутся в логи, argv и git.

## Ограничения

Это офлайн-снимок, а не полная база уязвимостей и не поддержка всех языков.
Проверяются только объявленные Maven/Gradle-зависимости и статически читаемый
Kotlin/YAML/properties. Kotlin/Gradle не исполняются. Runtime-конфигурация,
прокси, внешние фильтры и достижимость уязвимого кода не доказываются;
`expectedScopesVerified` остаётся `false`.

## Проверка на training snapshot

Изолированный PostgreSQL, локальный uvicorn, SHA
`fe1fcbe212669783480666b9b60147c8a8c6eef4`:

- 15/15 эталонных находок совпали по class/path/advisory;
- 6 безопасных контролей не дали срабатываний;
- добавлена 1 отдельная inconclusive `CF-OWNER-CHECK-UNPROVEN` — не дубль;
- итог: 15 подтверждённых reference + 1 unproven, вердикт `fail`;
- explicit legacy `engine: backend` и `engine: team_b` дают те же 15 reference;
- загруженный fake `vulnerability-database/vulnerabilities.json` проигнорирован:
  `CVE-9999-0001` и `example.invalid` в результатах отсутствуют;
- backend-only sample с path traversal и command injection дал обе находки
  (`KOTLIN-PATH-TRAVERSAL`, `KOTLIN-COMMAND-INJECTION`) в общем прогоне.

## Раскатка

- Исторические revision/results не трогать: новый код применяется только к
  будущим запускам.
- В образе нужен каталог `analyzers/advisory-snapshot/`; текущий
  `COPY analyzers ./analyzers`
  его уже включает.
- `scripts/nedra-bootstrap.sh` по умолчанию пишет
  `CG26_ADVISORY_DB=/app/data/team-b-advisories/vulnerabilities.json`. Пока этот
  файл не положен в volume, SCA будет работать со встроенным snapshot и
  предупреждением. Для чистого лога укажите
  `/app/analyzers/advisory-snapshot/trusted-advisories.json` или уберите переменную.
- Проверить после деплоя: `/api/runs/{id}/security-map` содержит 15 reference
  находок, `bundleHash` и identity совпадают с artifact/run, в `evidence` нет
  сырых секретов, а `coverage.status` честно `partial`.