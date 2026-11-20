# Детерминированное сравнение immutable bundles

`packages/comparison/index.mjs` — отдельный dependency-free шаг Node 22 после
анализа и публикации двух bundles. Он читает готовые факты B, возвращает существующий
`ComparisonDto` и не вызывает scanner, organizer source, AI, embeddings или provider.
Общие DTO и SQL не изменены. Это реализация поверх proposal v0.1, а не согласование
всего контракта за A/B.

## Вызов и typed API

```ts
import { compareBundles } from '../../packages/comparison/index.mjs';
import type { PublishedBundleEnvelope } from '../../packages/comparison/index.mjs';

// Получить через авторизованный A repository: published bundle + sealed manifest.
declare const baseline: PublishedBundleEnvelope;
declare const current: PublishedBundleEnvelope;
const comparison = compareBundles(baseline, current);
// Или передать options: { id: callerGeneratedUuid }.
```

Вызов синхронный, исходники/сеть/БД не читает, входы не изменяет. Декларации лежат
в `index.d.mts`. Можно передать два `AnalysisBundle`, но без published provenance и
manifests нельзя подтвердить публикацию, normalization или удаление: `comparable=false`,
items `inconclusive`, `deletedFiles=[]`. Некорректные ссылки, scope, fingerprint,
manifest, неканонический UUID (нужен lowercase), неизвестная schema или одинаковые run/snapshot отклоняются исключением;
это ошибка входа, не вычисленный результат сравнения. Разные workspace отклоняются.

Минимальная **предлагаемая обёртка**, только внутри этого модуля:

```ts
type PublishedBundleEnvelope = {
  bundle: AnalysisBundle;
  manifest: {
    complete: true;
    workspaceId: string;
    snapshotId: string;
    hash: string;
    files: Array<{
      path: string;
      sourceHash: string;
      byteLength: number;
      mediaType: string;
    }>;
  };
  provenance: {
    published: true;
    bundleHash: string;
    normalizationVersion: string;
    origin: 'scanner' | 'contract_scenario' | 'manual_fixture';
    expectedScopesVerified: boolean;
    domainPolicyApplicability: 'not_applicable' | 'applied' | 'unknown';
  };
};
```

Это дополнение ко входу, **не скрытая правка AnalysisBundle или ComparisonDto**.
A обязан брать `published`, `bundleHash`, manifest и origin из доверенного хранилища,
а не из пользовательского JSON. SHA shape и manifest content проверяются локально;
сам факт publication и bundle hash удостоверяет A по SQL: произвольный self-asserted
`published:true` не доказательство. Полный bundle hash из SQL включает persistence
представление, которое модуль не восстанавливает и заново не вычисляет.

Manifest проверяется по workspace/snapshot, `identity.snapshotManifestHash` и
пересчитанному SHA-256: сортировка path UTF-8 bytes (`COLLATE "C"`), поля
`{path,sourceHash,byteLength,mediaType}`, сериализация PostgreSQL `jsonb::text` как
в `snapshot_manifest_hash`. `manifestHash(files)` экспортирован для адаптера и
контрактных примеров. Не подставлять обычный `JSON.stringify(files)` вместо SQL hash.
Дубли path, неканонические пути, несоответствие evidence hash/bounds или coverage path
manifest отклоняются. Полнота manifest — обязательство intake/seal A; hash доказывает
состав переданных bytes, но не полноту относительно исходного ZIP.

Без options IDs детерминированны: SHA-256 domain-separated tuple workspace и обеих
run/snapshot IDs, затем UUIDv8 с 122 bits. Item ID включает comparison ID, status и
пару finding IDs. Сырые anchor, secret/HMAC или source не попадают в ID. Порядок items
и deletedFiles стабилен; finding не используется повторно. Это не UUIDv5, не токен
доступа и не глобальный поиск без workspace. Caller может выбрать comparison UUID.

CLI для отдельного шага A:

```sh
node packages/comparison/compare-cli.mjs baseline.published.json current.published.json
```

Он печатает только `ComparisonDto`; ошибка входа — stderr/exit 1. A сохраняет DTO
как draft comparison, явно маппит camelCase в SQL, добавляет workspace/run/snapshot
scope к каждому item, затем публикует comparison одной транзакцией. Publication
SQL guards сохраняются обязательными. Модуль не меняет baseline и старые findings.

## Совместимость и matching

Для `comparable=true` нужны published envelopes одного workspace, distinct runs
**и** snapshots, schema `0.1`, fingerprint `fp/v1`, одинаковый непустой
`normalizationVersion`, `expectedScopesVerified=true` и одинаковый origin.
`manual_fixture` всегда несопоставим. Два `contract_scenario` допустимы только как
проверка механики: warning `contract scenario, not scanner output` остаётся в reasons,
не становится настоящим scan. Реальному scanner origin доверяет A, не CLI пользователя.

Exact equality всех `AnalysisSignature` полей: parser/rules/advisory/config
versions **и** hashes, HMAC key version, domain policy revision/version/hash.
Дополнительно ruleVersion/ruleFamily для наблюдаемых rule IDs не должны конфликтовать.
`domainPolicyApplicability=unknown` блокирует сравнение; `applied` требует policy в
signature, `not_applicable` — null policy. Одинаковые null без явного not_applicable
недостаточны. B/A гарантируют согласованную applicability для полного набора правил.
AI provider/model/prompt не входят в статическую совместимость. Artifact/file hashes
между runs могут различаться. Workflow revision хранится в identity, но статические
настройки анализа должны целиком входить в scanConfigHash.

Несовместимость даёт `comparable=false`: items только `inconclusive` либо
`file_deleted`, если два полных manifests достоверно подтверждают отсутствие пути.
Никакой compatibility map/rename map в этой версии не поддерживается.

Matching сначала блокирует **все** повторяющиеся fingerprints и coarse anchors
в любой стороне: первый UUID или ближайшая строка не выбирается. Anchor ключ —
`(category,ruleId,path,symbolKey,role)`, ровно консервативная граница SQL guard.
Затем уникальный fingerprint → `persistent`; уникальный coarse anchor с другим
fingerprint → `changed`. В частности, смена normalizedContextHash при том же anchor
не превращается в `resolved + introduced`. Ни line, ни расстояние по строкам,
ни AI/similarity не являются identity.

Все обычные статусы требуют complete scanner outcome соответствующей category,
complete scanner coverage и checked category/path/rule в обеих сторонах, а также
complete graph coverage без ошибок/пропусков/unresolved symbols/лимитов. Предупреждения
coverage самого finding блокируют доказанный статус. Новый path освобождает только
от baseline checked path, но не от baseline complete category и полноты expected scope.
Это намеренно строже SQL; даже unrelated graph gap пока приводит к inconclusive.

| Результат | Достаточное основание и пример причины |
|---|---|
| `persistent` | Один fingerprint и anchor, совместимый полный анализ; «сдвиг строк не меняет находку» |
| `changed` | Один coarse anchor, другой контекст/основание; «это изменение, а не доказанное исправление» |
| `resolved` | Файл в обоих manifests; оба file/rule checked, complete outcomes/graph, нет current fingerprint **и coarse anchor** |
| `introduced` | Нет baseline fingerprint/anchor; оба scopes checked или новый manifest path + complete baseline category; `new_scope` в reason |
| `inconclusive` | Например «После: нет checked для src/Query.kt / sql-interpolation», «покрытие графа неполное», повторяющийся anchor, несовместимые signatures |
| `file_deleted` | Только baseline finding и отсутствие path в полном current manifest; «переименование не исключено» |

`deletedFiles` — anti-join **всех** manifest paths, включая файлы без findings.
Это отдельный результат, не число исправлений. Одновременно удалённый старый путь
и новый путь без rename map: baseline `file_deleted`, новая находка `inconclusive`,
потому что это может быть перенос. Отсутствие finding никогда не означает удаление.
Неоднозначный baseline anchor остаётся inconclusive, даже если deletedFiles содержит
его путь; для удаления весь manifest всё равно доступен отдельно.

## Где полноту гарантируют B и A

`validateBundle` проверяет межобъектные связи, scope/signature и рецепт fingerprint;
модуль вызывает его без source bytes. JSON Schema, redaction и реальные byte/line
координаты должны быть проверены ingestion до удаления source. Счётчик filesVisited,
пустой findings/scopes массив и общий succeeded сами по себе не являются основанием.
Engine требует конкретный checked path/rule для существующего scope.

Однако никакой consumer не может вывести **отсутствующий ожидаемый scope** только
из списка переданных checked rows. До `expectedScopesVerified=true` B обязан построить
полное ожидаемое множество category/path/rule по immutable manifest, config/exclusions,
применимости правил и graph dependencies; сравнить его с outcome/coverage, сохранить
ошибки и лимиты. A должен проверить это основание до publish и выдавать attestation
только для опубликованных bytes. SQL count(rows) и graph `complete` с пустым массивом
тоже не восстанавливают забытый scope.

Для исчезновения SQL interpolation checked scope B должен означать реально
инспектированный sink/rule-not-matched либо эквивалентную полную scoped проверку.
DTO `CoverageScope` пока не содержит отдельного sink outcome. Если B не может
гарантировать такой смысл checked или полноту extraction/graph, он обязан передать
partial/error и не выдавать attestation. Для будущего granular разрешения предлагается
дополнение B с typed inspected-anchor outcomes; оно здесь не выдумано и общий DTO
не расширен. C не доказывает reachability, не парсит Kotlin и не считает наличие `?`
доказательством параметризации.

В `index.d.mts` отдельно обозначен `ComparisonProvenanceProposal` для возможного
сохранения signature/manifest references и algorithm version рядом с comparison.
Это тип предложения, не возвращаемое поле и не действующая миграция. Сегодня A
получает основания через immutable FK runs/bundles и хранит provenance envelope
в своём trusted adapter. Утверждение дополнения остаётся за A/B/C.

## Что реально наблюдалось

Настоящих before/after runs B в выданном worktree нет. Исходный безопасный контроль
из `docs/security-map/COMPARISON.md` не after исправленного sink. Organizer source,
Kotlin/Gradle, scanner, SQL publication, provider и embeddings не запускались.
Статическое исчезновение паттерна, runtime/exploit и сохранение функциональности
после SQL-параметризации не заявляются.

Живая одноразовая проверка Node 22 на синтетических DTO, **contract scenario, not
scanner output**: 20 основных и 16 дополнительных probes прошли. Наблюдались persistent при сдвиге строк, changed
при смене context, resolved при полном checked removal, inconclusive при потере
scanner/graph coverage, отдельный file_deleted, introduced на новом manifest path,
signature/normalization mismatch, два вида duplicate anchor/fingerprint, отсутствие
scope при filesVisited/пустом findings, raw DTO и manual fixture, возможный rename,
неподтверждённые expected scopes. Подмена manifest, одинаковые run/snapshot и разные
workspace отклонены. Входные объекты не изменялись, повторный вызов дал тот же DTO.
Дополнительно проверены отдельные parser/rules/advisory/config/HMAC изменения,
partial/failed/timeout/unsupported outcomes, перестановка scanner envelopes,
file_deleted при несовместимой signature и SQL hash пустого manifest.

CLI на синтетической паре вывел changed с предупреждением о происхождении; вызов
без аргументов дал usage/exit 1. Репозиторный illustrative-bundle как raw input дал
comparable=false и inconclusive. `node --check` и `git diff --check` прошли. Typed API
проверен TypeScript 5.9.3 из существующего локального cache без установки в проект и
без сети: declaration + strict consumer, включая отклонение неполной обёртки.
Одноразовый probe и tooling не включены в продукт, ритуальной unit suite нет.

Ограничения первого показа: PostgreSQL round-trip manifest serialization и настоящая
публикация comparison в этом участке не измерены; доверенный adapter A должен сверить
выданный SQL manifest hash. Первый настоящий SQL proof loop ждёт B before/after facts
и подтверждённой полноты expected scopes. До этого UI может показывать механизм и
его ограничения, но не заявлять измеренный fix.
