# Payment Gateway: умышленные уязвимости

15 находок (5 SECRET, 5 SCA, 5 SAST) и 6 контрольных примеров для валидации ложных срабатываний.

| ID | Категория | Сложность | Где | Что уязвимо |
| --- | --- | --- | --- | --- |
| P-SEC-01 | SECRET | EASY | [MerchantVaultClient.kt:4](../payment-service/src/main/kotlin/com/payment/integration/MerchantVaultClient.kt#L4) | Пароль хранилища учетных записей зашит в коде. |
| P-SEC-02 | SECRET | EASY | [StripeCardClient.kt:7](../payment-service/src/main/kotlin/com/payment/integration/StripeCardClient.kt#L7) | API-ключ шлюза карт напрямую зашит в заголовок `X-Api-Key`. |
| P-SEC-03 | SECRET | MEDIUM | [application.yml:14](../payment-service/src/main/resources/application.yml#L13) | В конфигурации прописан bearer-токен для подтверждения выплат. |
| P-SEC-04 | SECRET | MEDIUM | [LedgerClient.kt:8](../payment-service/src/main/kotlin/com/payment/integration/LedgerClient.kt#L8) | Клиент расчётного журнала использует токен по умолчанию при отсутствии переменной окружения. |
| P-SEC-05 | SECRET | HARD | [SettlementAuthHeaders.kt:7–10](../payment-service/src/main/kotlin/com/payment/integration/SettlementAuthHeaders.kt#L7-L10) | Токен клиринга собирается из двух фрагментов перед отправкой запроса. |
| P-SCA-01 | SCA | EASY | [build.gradle.kts:31](../payment-service/build.gradle.kts#L31) | `com.fasterxml.jackson.core:jackson-databind:2.9.8` — десериализация полиморфных типов (CVE-2020-11619). |
| P-SCA-02 | SCA | EASY | [build.gradle.kts:32](../payment-service/build.gradle.kts#L32) | `org.yaml:snakeyaml:1.30` — DoS при обработке рекурсивных структур (CVE-2022-25857). |
| P-SCA-03 | SCA | MEDIUM | [build.gradle.kts:33](../payment-service/build.gradle.kts#L33) | `commons-io:commons-io:2.6` — обход каталога в FileNameUtils (CVE-2021-29425). |
| P-SCA-04 | SCA | MEDIUM | [build.gradle.kts:36](../payment-service/build.gradle.kts#L36) | `org.apache.httpcomponents:httpclient:4.5.12` — некорректная обработка некорректных URI (CVE-2020-13956). |
| P-SCA-05 | SCA | HARD | [build.gradle.kts:38](../payment-service/build.gradle.kts#L38) | `net.minidev:json-smart:2.4.8` — переполнение стека при синтаксическом разборе JSON (CVE-2023-1370). |
| P-SAST-01 | SAST | EASY | [TransactionSearchController.kt:13–14](../payment-service/src/main/kotlin/com/payment/transaction/TransactionSearchController.kt#L13-L14) | Параметр referenceTag подставляется в SQL без параметризации. |
| P-SAST-02 | SAST | EASY | [LegacyCardProcessorClient.kt:14](../payment-service/src/main/kotlin/com/payment/integration/LegacyCardProcessorClient.kt#L13) | TLS-клиент отключает валидацию сертификатов через пустой TrustManager. |
| P-SAST-03 | SAST | MEDIUM | [PaymentCorsConfig.kt:13–14](../payment-service/src/main/kotlin/com/payment/config/PaymentCorsConfig.kt#L13-L14) | CORS разрешает любой origin с учетными данными для приватной финансовой сводки. |
| P-SAST-04 | SAST | MEDIUM | [PaymentSecurityConfig.kt:18](../payment-service/src/main/kotlin/com/payment/config/PaymentSecurityConfig.kt#L18) | Экспорт клиринга доступен публично без аутентификации. |
| P-SAST-05 | SAST | HARD | [WalletStatementReader.kt:10–11](../payment-service/src/main/kotlin/com/payment/wallet/WalletStatementReader.kt#L10-L11) | Клиент может просмотреть выписку по ID без проверки принадлежности мерчанту (IDOR). |
