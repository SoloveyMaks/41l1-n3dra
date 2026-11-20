# Данные и карта безопасности — пакет C

Статус: **proposal v0.1 для согласования A/B/D**. PostgreSQL + pgvector выбраны пользователем; подтверждений остальных участников этот документ не подменяет. Пакет включает схему, DTO, React Flow SecurityMap с ручным pickup-preview и модуль сравнения immutable bundles. API, анализаторы и подключение к основному интерфейсу ещё требуют A/B.

## Что открыть

| Материал | Для чего |
| --- | --- |
| [Контракт и вопросы участникам](data-contracts/README.md) | Identity, signature, fingerprint, координаты, границы доверия |
| [JSON Schema](data-contracts/contracts.schema.json) / [TypeScript](data-contracts/contracts.ts) | Finding, EvidenceRef, CodeNode, CodeEdge, Coverage и общие envelopes |
| [Порядок миграций](../db/README.md) | PostgreSQL, владельцы записи, immutable snapshots и atomic publish |
| [SQL ↔ DTO](data-contracts/mapping.md) | Сохранение и обратное чтение без потери смысла |
| [Правила сравнения](data-contracts/comparisons.md) | Сопоставимость проверок, удалённые файлы и неоднозначность |
| [Первый путь](security-map/INTEGRATION.md) / [пример](security-map/pickup-path.illustration.json) | Pickup export, основания каждой стрелки, ticket/catalog как следующие истории |
| [Проекция карты в DTO](security-map/MAPPING.md) | Разница ручной иллюстрации и production bundle |
| [Компонент карты](security-map/COMPONENT.md) | Props, peer dependencies, запуск preview и браузерные проверки |
| [Comparison engine](data-contracts/COMPARISON-ENGINE.md) | Typed API, публикация через A, причины консервативного сравнения |
| [Проверка БД и DTO](data-contracts/verification.md) / [проверка источников карты](security-map/VERIFICATION.md) | Что реально запускали и что этим не доказано |

## Что согласовать

| Кто | Конкретное решение |
| --- | --- |
| A — backend / основной интерфейс | Workspace auth и роли БД, artifact TTL/store, masked-source endpoint, атомарное сохранение и внешний ключ идемпотентности запроса |
| B — анализаторы | По одному реальному Finding каждой категории, CodeFactsBundle, vocabulary связей, semantic anchors и ожидаемое множество file/rule coverage |
| Пользователь + A/B | Источник и версия политики для pickup и owner-check; кто утверждает ограничения и исключения ролей |
| D — AI | Grounding/validation статусы; один embedding provider/model/version/dimension/metric и версии подготовки текста |
| C — данные / карта | Внести согласованные изменения в общий контракт и миграции, сохранить факты B, реализовать SecurityMap по DTO |

Для подтверждения нужны решения по полям и по одному примеру payload, а не общий ответ «всё ок». Непроверенный совет AI не получает статус проверенного примера; смена embedding spec не меняет факты анализа.

## Ближайший рабочий путь

- [ ] Зафиксировать ответы A/B/D и версию общего контракта; текущий proposal не считать утверждённым.
- [ ] A создаёт immutable snapshot/run, B передаёт факты pickup export с настоящими версиями и coverage.
- [ ] A/C сохраняют bundle атомарно; повтор того же результата не создаёт новых фактов.
- [ ] C строит карту из сохранённых фактов, A подключает её в основной интерфейс.
- [ ] Каждая стрелка открывает правильный masked fragment; чужой workspace/run и истёкший источник дают явный безопасный отказ.
- [ ] После этого B готовит два SQL-сценария до/после параметризации; C сравнивает два настоящих результата без объявления fix при потере покрытия.
- [ ] Когда D даст проверенный embedding spec и появятся проверенные примеры, подключить exact retrieval как необязательный шаг.

Ручной pickup-путь — иллюстрация контракта с проверенными ссылками на исходник. Это не scanner result, не работающий UI и не доказательство эксплуатации. Синтетический vector probe проверяет SQL-механику; он не выбирает модель и не доказывает пользу поиска.

## Передача следующего участка

Компонент карты собран из lockfile и проверен в браузере на ручном pickup-разборе; независимое ревью production DTO gate/import не выявило блокеров. Comparison engine проверен на синтетических contract scenarios, включая rename со сменой контекста и явные partial/unknown edges: оба случая запрещают доказанное исправление. Четыре manifest hash сверены с реальным PostgreSQL. Это готовые к подключению модули C, но не сквозная приёмка приложения.

Следующий шаг A/B: передать настоящие published bundle + sealed manifests и masked-source API, подключить SecurityMap и получить первый SQL before/after из двух реальных scanner runs. До этого не отмечаем выполненными пункты о живом source endpoint или scanner proof loop.
