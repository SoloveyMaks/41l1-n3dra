# Первый путь карты — предложение к согласованию

Пакет содержит **ручную иллюстрацию контракта**, не результат анализатора и не эксплуатацию. Этот документ описывает разбор исходника. Подключаемый React Flow-компонент и его живые проверки теперь описаны в [COMPONENT.md](COMPONENT.md); backend и scanner в этом участке не создаются. Область C — хранение фактов анализатора B, карта доступа/данных, сравнение и retrieval; A подключает API/основной интерфейс, D — AI/embedding adapter. Решения проекта собраны в [PROJECT-DECISIONS.md](../../PROJECT-DECISIONS.md). Все поля ниже требуют сверки с владельцами A/B/D и общими DTO/SQL.

## Что лежит рядом

- `pickup-path.illustration.json`: 7 узлов, 6 связей первого пути, Finding, 21 EvidenceRef, отдельная policy assumption; точные источники ticket/catalog и SQL-сравнения.
- `pickup-layout.illustration.json`: отдельное состояние представления. Не workflow revision и не факты кода.
- `COMPARISON.md`: правила честного статического сравнения; настоящих before/after scan здесь нет.
- `MAPPING.md`: точная проекция illustration в proposal schemaVersion `0.1`, сверенная с актуальными DTO другого worker; не production bundle.
- `VERIFICATION.md`: результат одноразовой сверки исходников и ссылок, ограничения проверки.

Источник — `training-project/` из [публичного snapshot TBagirov/training](https://github.com/TBagirov/training/tree/fe1fcbe212669783480666b9b60147c8a8c6eef4). В JSON есть относительный корень и manifest файлов с SHA-256 и его отдельным hash. Скачанную копию для воспроизведения можно положить в `research/organizer-training/fe1fcbe212669783480666b9b60147c8a8c6eef4/`; в пакет она не включена. Directory label сам по себе не доказывает Git commit. SHA-256 manifest **не** равен hash исходного ZIP.

`illustration-*` IDs не обозначают настоящие workspace/run/snapshot записи в БД. `artifactId`, `artifactHash`, `workflowRevisionId` и scanner versions неизвестны и оставлены `null`; это fixture envelope, не допустимый production publish bundle. После согласования потребуется загрузка через A, настоящий immutable run и bundle B с настоящей signature. Fixture нельзя экспортировать как найденные scanner findings или показывать в demo без подписи `manual_source_review / contract illustration`.

## Первый прослеживаемый путь

| Стрелка | Основание в fixture | Что установлено |
| --- | --- | --- |
| anonymous → GET `/api/pickup/export` | `ev-pickup-permit`, `ev-pickup-route` | Статический кандидат публичного GET; HTTP-запрос не делался |
| route → первое применимое permitAll | `ev-pickup-order`, `ev-pickup-route` | Catalog matcher не совпадает, точное разрешение pickup идёт раньше общего `/api/pickup/**` |
| permitAll → `PickupExportController.export` | `ev-pickup-permit`, `ev-pickup-route`, `ev-security-scope` | Совпадают HTTP method/path и mapping контроллера |
| export → SQL | `ev-pickup-query` | Прямой `jdbc.queryForList`, возвращаемый из метода |
| SQL → данные | `ev-pickup-data`, `ev-ticket-schema`, `ev-pickup-schema` | JOIN по ticket_id; ticket_code, customer_id, counter_note; WHERE отсутствует в этом SQL |
| данные → potential Finding | `ev-pickup-permit`, `ev-pickup-data`, `ev-domain-context`, `ev-pickup-role` | Интерпретация доступа и предполагаемой чувствительности, **не вызов функции** |

Порядок правил трактуется по семантике Spring `authorizeHttpRequests`: первое совпавшее правило применяется, поздний MECHANIC matcher не отменяет ранний permitAll. Это явно указанная ручная интерпретация DSL, а не доказательство, что проверены runtime Spring, вся конфигурация и все guards. `resolution=resolved` означает разрешённый вручную source fact в этих границах, не подтверждённую эксплуатацию. B должен поддержать такую семантику в parser/rules либо вернуть partial/unknown.

**Чувствительность и policy раздельны.** SQL и schema подтверждают связь с клиентом и примечанием выдачи. README:5 описывает гостя, клиента и сотрудников; более поздняя роль MECHANIC указывает на вероятное намерение ограничить pickup. Ни одно из них не является строгим согласованным запретом публичной выгрузки. В `policy-pickup-proposal` сохранены источник, `unapproved_assumption`, пустые approval/identity/exception поля и hash предложения. Finding остаётся `potential`, HIGH — иллюстративная оценка, которую подтверждает B; approved policy и formal data classification отсутствуют. Public само по себе не означает vulnerability.

`gap-additional-guards` явно имеет `resolution=unknown`, `toId=null` и причину: дополнительная runtime авторизация не исследована. Это карточка пробела рядом с handler, а не придуманное ребро вызова. Наличие source refs обозначает границу осмотра, а не доказательство отсутствия guards во всём приложении. На экране unknown нельзя спрятать зелёной галочкой.

## Ещё два пути — только контур

Все ranges и hashes находятся в массиве `evidence`; контуры — в `scenarioOutlines`.

- **Ticket:** `RepairSecurity.kt:20` → `RepairTicketController.kt:10–15` → `RepairTicketReader.kt:10–14`. Prefix `/api/customer` + `/tickets/{ticketId}`, передаётся один ticketId, query параметризован и фильтрует только `ticket_id`. Matcher требует authenticated, но не доказывает роль CUSTOMER. Отдельные `summary(Principal)` (:17–19) и `countForCustomer` (:16–20) не связывают identity с `getTicket`. Missing owner relation показывается unknown; Finding — potential, пока нет policy: ресурс, owner field `customer_id`, trusted Principal→customer mapping, исключения сотрудников, источник/автор/версия/hash. Подстановка `?` решает интерполяцию значения, но не доступ к объекту.
- **Catalog:** `RepairSecurity.kt:17`, `ServiceCatalogController.kt:6–9`, `README.md:5`. Публичный GET возвращает фиксированный список услуг; это поддержанный локальный контрпример без Finding. Отсутствие Finding не доказывает безопасность всего приложения. Ни runtime, ни полное scanner coverage не заявлены.

## Стык с общими DTO и хранением

Это ручной пример будущего `SecurityMapDto` со своими `CodeNode`/`CodeEdge`/`EvidenceRef`; **не** workflow DAG. В нём нет executor ports, trigger state, retry или порядка запуска. Имена `supports_finding` и `first_matching_authorization` принадлежат illustration, а не enum production `0.1`. По [MAPPING.md](MAPPING.md) остаются 6 code nodes и 5 code edges; Finding связывается через `findingNodeLinks`, не code_node kind finding. A не передаёт карту workflow executor.

До слияния общий владелец DTO/SQL согласует:

| Поле/решение | Требование для интеграции |
| --- | --- |
| Identity | workspace/run/snapshot scope проверяется сервером и FK, а не доверяется клиенту; fixture IDs заменяются настоящими |
| Evidence | `sourceHash` — SHA-256 полных исходных bytes файла; строки/колонки с 1, Unicode code points, конец колонки исключён; bytes `[start,end)` без перекодирования/CRLF normalizing |
| Graph references | IDs уникальны в run; every edge/node/finding ref в том же run/snapshot; publish bundle целиком или видимый partial |
| Finding trace | В illustration — edge IDs; в `0.1` — `traceEvidenceIds`: 10 source refs по плану MAPPING, не Edge UUID |
| Unknown relation | В illustration — `unresolvedRelations`; в `0.1` — два Diagnostic с singular evidenceId и сохранённой причиной; nullable SQL edge не создаётся |
| Policy | `0.1` различает role_access(resource,allowedRoles) для pickup и ownership(resource,ownerField,trustedIdentity,roleExceptions) для ticket; assumption остаётся без approval |
| Producer | `manual-source-review/1` сейчас; production — реальные parser/rules versions B; manual evidence не становится scanner evidence автоматически |
| Signature | `not_a_scanner_run` и null versions допустимы лишь в illustration envelope; production signature обязательна |
| Layout | Отдельный read/write DTO по map identity/view, без изменения facts или workflow revision |

После интеграции общий контракт лежит в `docs/data-contracts/` рядом с картой; структура mapping сверена с proposal `0.1`, точные hashes и границы проверки — в `VERIFICATION.md`. Формального согласования A/B/D нет. Остались настоящие IDs/signature/coverage, источник и версия policy и подтверждение семантики edge kinds B; полный production payload карты не создавался и его SQL ingestion не проверялся.

## Masked source: контракт операции A

Предлагаемая операция `A.maskedEvidence`:
`GET /api/workspaces/{workspaceId}/runs/{runId}/snapshots/{snapshotId}/evidence/{evidenceId}/source`.
Это предложение маршрута, **endpoint не реализован**; A закрепляет точный URL. UI передаёт только scoped IDs, не локальный абсолютный путь, произвольный file/range или `file://`.

Сервер A проверяет session workspace, связность run→snapshot→evidence, path containment и отсутствие symlink traversal; location берётся из сохранённого evidence. Проверяет sourceHash до выдачи. Ответ: scope, evidenceId, sourceHash, исходный location, masked fragment, redaction metadata/version, безопасная диагностика. Hash mismatch → `409 source_changed`, истёкший artifact → `410 source_unavailable`, отсутствующий/чужой scope → одинаковый `404 evidence_not_available`; авторизация проверяется до чтения bytes. Истечение TTL не превращает источник в пустой успешный ответ; сохранённый redacted snippet можно показать с отметкой, что исходник недоступен.

Маскировка **до ответа**: полное значение секрета и все literal-части, из которых его можно восстановить, в том числе вне выбранного range и в добавленном контексте. Нельзя считать range безопасным только потому, что SECRET finding на другой строке. По умолчанию контекст не выдаётся; запрошенный соседний контекст проходит ту же redaction pipeline. Если redaction coverage не позволяет безопасно раскрыть fragment, вернуть `source_redaction_incomplete`, а не raw source. Никаких raw ZIP, data values, секретов в URL, SSE, tooltip, clipboard или error log.

Наши 21 range вручную просмотрены: чувствительных literal значений в них нет; `redactedSnippet` содержит только этот range, `contextIncluded=false`. Это локальная проверка указанных ranges, не готовый секретный redactor для произвольного проекта. Bytes/колонки относятся к оригиналу: masked текст другой длины не меняет source location; для подсветки A возвращает safe coordinate mapping либо маски той же длины. Клиент не вычисляет source bytes из отображённой строки.

## React Flow SecurityMap — стык с A

Исходное предложение props ниже сохранено как контекст; действующие типы, callbacks и команды запуска — в [COMPONENT.md](COMPONENT.md). Props: `identity`, `data: SecurityMapDto | null`, `layout: SecurityMapLayoutDto`, `loadState: loading|ready|empty|partial|error`, `selectedFindingId?`, `selectedNodeId?`, `selectedEdgeId?`, `error?: SafeError`. Отдельно `sourceState` с pending/error/unavailable для выбранного evidence, чтобы ошибка source не уничтожала карту.

Callbacks: `onSelectFinding(findingId)`, `onSelectNode(nodeId)`, `onSelectEdge(edgeId)`, `onOpenEvidence({workspaceId, runId, snapshotId, evidenceId})`, `onLayoutChange(layout)`, `onRetry()`, `onRetrySource(evidenceId)`. Они выбирают/открывают refs, не создают факты и не запускают scanner. A владеет fetching/session/router; C — отображением карты и панели доказательств. На смене identity A отменяет запросы/сбрасывает selection и source cache; запоздалый ответ другого run не рисуется.

Панель edge показывает вид/основание связи, location, producer, resolution/reason, policy assumption, coverage и ограничения; выбор каждого evidence открывает только masked endpoint A. Unknown заметен текстом/пунктиром, не одним цветом; в illustration `supports_finding` помечен как интерпретация; в `0.1` её заменяет панель Finding через `findingNodeLinks`. Canvas positions хранятся в layout; перетаскивание не изменяет DTO facts.

| Состояние | Поведение |
| --- | --- |
| loading | Skeleton и identity; нет зелёного «чисто» и фиктивных узлов |
| empty | «Для этого запуска нет поддержанных связей» + coverage; не «уязвимостей нет» |
| partial | Подтверждённые facts видны, пробелы/unknown и причины рядом; доступна таблица evidence |
| error | Безопасная причина и retry; старые данные только с явной старой identity, без смешивания |

## Приёмка следующей интеграции

- [ ] Все A/B/C/D подтвердили общую schemaVersion, enums, signature, policy и masked-source operation; приложение ещё не требуется для согласования.
- [ ] A создал реальный immutable run, B выдал настоящий bundle; после сохранения C перечитал те же refs без дублей и без manual→scanner переименования.
- [ ] Пять code edges и Finding panel по `findingNodeLinks` сохраняют доказательства всех шести иллюстративных стрелок; минимум три перехода к scoped source пройдены в живом UI, включая policy basis и SQL.
- [ ] Чужой workspace/run/snapshot не открывает evidence; mismatch/TTL/redaction gap не выдаётся как успешный пустой source.
- [ ] Ticket owner-policy gap и публичный catalog показаны без усиления вывода; unknown не скрыт.
- [ ] SQL до/после получены двумя реальными статическими run; удаление файла и пропуск повторного scan не названы fix (см. `COMPARISON.md`).

**Осталось решить:** реальные scope/signature/coverage и семантику edge vocabulary B, источник и утверждение domain policy, severity B, маршрут A и redaction coverage, подключение DTO/SQL; преобразование trace/enums/ref структуры описано в MAPPING. Этот пакет делает эти решения проверяемыми; не отмечает будущую интеграцию выполненной.
