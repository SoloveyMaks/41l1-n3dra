# Proof Loop: что именно доказывает статус

Сравнение — два published immutable run одного workspace. Сначала exact match static signature: parser/rules/advisory/config versions **и hashes**, domain policy revision/version/hash, HMAC key version. Разные ZIP/file/snapshot hashes ожидаемы. AI model не входит в статическую signature. Несовместимость → `comparable=false`, причина, items только inconclusive/file_deleted; перескан обеих версий возможен отдельно.

| Ситуация | Статус | Основание |
|---|---|---|
| Комментарии сдвинули строки, один semantic anchor | persistent | Один fingerprint в каждом run; линия не ключ |
| Тот же anchor, evidence изменилось | changed | Однозначная связь B по symbol/context/role; явная причина, не nearest line |
| Паттерн исчез, файл остался | resolved | Полностью checked затронутые file/rule в обоих run; текущий fingerprint отсутствует; никакой ambiguous match |
| Файл удалён | file_deleted | path отсутствует в current manifest; отдельно от исправления |
| Паттерн появился | introduced | baseline проверил соответствующие family/file/rule или path доказуемо новый; точная причина |
| Parse error/skip/timeout/budget/missing outcome | inconclusive | Нет доказательства отсутствия; общий succeeded не спасает |
| Два одинаковых anchor или rename без карты B | inconclusive | Первый UUID/номер строки не выбираем |
| Правила/advisory/HMAC/policy/config поменялись | inconclusive | Иной анализ, не эффект исправления |

C не строит второй scanner и не угадывает source reachability. SQL при publication comparison проверяет published bundles/signature, форму baseline/current refs, required matchingBasis и для resolved — checked path/rule в обоих run + отсутствие current fingerprint. `persistent` требует одинаковый fingerprint. `introduced` требует complete baseline category и checked current path/rule; если path был в baseline manifest, checked baseline path/rule также обязателен. Факт единственности semantic matching/полноты expected scopes и changed rationale проверяет C/B до INSERT: SQL не доказывает семантику Kotlin. Дополнительно `resolved` запрещён, если в current остаётся finding по тому же coarse anchor `(category,ruleId,path,symbolKey,anchorRole)`, даже с другим context hash/fingerprint: нужен changed или inconclusive. Симметричная проверка запрещает introduced при уже существующем coarse anchor. Это консервативная защита: несколько разных sink внутри одной функции могут потребовать inconclusive, пока B не утвердил более точный anchor. Неподдержанная логика остаётся inconclusive. Правила SQL сознательно консервативны: изменившийся parser hash требует перескана, даже если change кажется косметическим.

Удалённые файлы ComparisonDto.deletedFiles — anti-join всех manifest paths, включая файлы без finding; file_deleted items относятся только к baseline findings. Перемещение файла сейчас = deletion/new path, пока B не дал доказуемый rename mapping. Человек выбирает baseline явно; его нельзя автоматически обновлять по непроверенному PR.

Ни resolved, ни vetted example не доказывают отсутствие всех уязвимостей или сохранение функциональности после правки. Runtime/exploit/Gradle здесь не запускаются. AI совет не меняет finding status или comparison result.
