# Fleet Telemetry: умышленные уязвимости

15 находок (5 SECRET, 5 SCA, 5 SAST) и 6 контрольных примеров для валидации ложных срабатываний.

| ID | Категория | Сложность | Где | Что уязвимо |
| --- | --- | --- | --- | --- |
| F-SEC-01 | SECRET | EASY | [MqttBrokerSettings.kt:4](../telemetry-service/src/main/kotlin/com/fleet/integration/MqttBrokerSettings.kt#L4) | Пароль подключения к брокеру MQTT зашит в кодовой базе. |
| F-SEC-02 | SECRET | EASY | [GpsRoutingClient.kt:7](../telemetry-service/src/main/kotlin/com/fleet/integration/GpsRoutingClient.kt#L7) | Ключ доступа API картографического сервиса зашит в заголовок `X-Api-Key`. |
| F-SEC-03 | SECRET | MEDIUM | [application.yml:14](../telemetry-service/src/main/resources/application.yml#L14) | В конфигурации прописан bearer-токен для приема телеметрии. |
| F-SEC-04 | SECRET | MEDIUM | [CanBusDecoderClient.kt:8](../telemetry-service/src/main/kotlin/com/fleet/integration/CanBusDecoderClient.kt#L8) | Клиент декодера CAN-шины использует токен по умолчанию при отсутствии переменной окружения. |
| F-SEC-05 | SECRET | HARD | [DispatchAuthHeaders.kt:7–10](../telemetry-service/src/main/kotlin/com/fleet/integration/DispatchAuthHeaders.kt#L7-L10) | Токен диспетчерского центра склеивается из двух строковых литералов. |
| F-SCA-01 | SCA | EASY | [build.gradle.kts:31](../telemetry-service/build.gradle.kts#L31) | `org.apache.commons:commons-compress:1.24.0` — зацикливание при разборе DUMP-архива (CVE-2024-25710). |
| F-SCA-02 | SCA | EASY | [build.gradle.kts:32](../telemetry-service/build.gradle.kts#L32) | `com.hazelcast:hazelcast:5.1.2` — обход аутентификации в кластере (CVE-2022-36437). |
| F-SCA-03 | SCA | MEDIUM | [build.gradle.kts:33](../telemetry-service/build.gradle.kts#L33) | `com.thoughtworks.xstream:xstream:1.4.15` — выполнение произвольного кода через десериализацию XML (CVE-2021-39149). |
| F-SCA-04 | SCA | MEDIUM | [build.gradle.kts:36](../telemetry-service/build.gradle.kts#L36) | `org.mybatis:mybatis:3.5.5` — RCE при десериализации пользовательских объектов (CVE-2020-26945). |
| F-SCA-05 | SCA | HARD | [build.gradle.kts:38](../telemetry-service/build.gradle.kts#L38) | `org.json:json:20230227` — исчерпание памяти при парсинге вложенных структур (CVE-2023-5072). |
| F-SAST-01 | SAST | EASY | [TelemetrySearchController.kt:13–14](../telemetry-service/src/main/kotlin/com/fleet/telemetry/TelemetrySearchController.kt#L13-L14) | Параметр sensorSerial конкатенируется в SQL без экранирования. |
| F-SAST-02 | SAST | EASY | [OtaFirmwareClient.kt:14](../telemetry-service/src/main/kotlin/com/fleet/integration/OtaFirmwareClient.kt#L14) | Клиент загрузки прошивок OTA отключает проверку доверия сертификатов. |
| F-SAST-03 | SAST | MEDIUM | [FleetCorsConfig.kt:13–14](../telemetry-service/src/main/kotlin/com/fleet/config/FleetCorsConfig.kt#L13-L14) | CORS разрешает wildcard origin с авторизацией для телеметрии автопарка. |
| F-SAST-04 | SAST | MEDIUM | [FleetSecurityConfig.kt:18](../telemetry-service/src/main/kotlin/com/fleet/config/FleetSecurityConfig.kt#L18) | Дамп телеметрии /api/telemetry/raw-dump открыт публично без проверки роли. |
| F-SAST-05 | SAST | HARD | [DiagnosticLogReader.kt:10–11](../telemetry-service/src/main/kotlin/com/fleet/telemetry/DiagnosticLogReader.kt#L10-L11) | Пользователь может прочитать диагностический отчет другого клиента без проверки принадлежности (IDOR). |
