# Healthcare Records: умышленные уязвимости

15 находок (5 SECRET, 5 SCA, 5 SAST) и 6 контрольных примеров для валидации ложных срабатываний.

| ID | Категория | Сложность | Где | Что уязвимо |
| --- | --- | --- | --- | --- |
| H-SEC-01 | SECRET | EASY | [Hl7IntegrationSettings.kt:4](../clinical-service/src/main/kotlin/com/health/integration/Hl7IntegrationSettings.kt#L4) | Пароль подключения к базе HL7 прописан в коде. |
| H-SEC-02 | SECRET | EASY | [TelehealthSessionClient.kt:7](../clinical-service/src/main/kotlin/com/health/integration/TelehealthSessionClient.kt#L7) | API-ключ видеосервиса телемедицины зашит в заголовок `X-Api-Key`. |
| H-SEC-03 | SECRET | MEDIUM | [application.yml:14](../clinical-service/src/main/resources/application.yml#L14) | В конфигурации прописан bearer-токен для синхронизации EHR. |
| H-SEC-04 | SECRET | MEDIUM | [LabTelemetryClient.kt:8](../clinical-service/src/main/kotlin/com/health/integration/LabTelemetryClient.kt#L8) | Клиент лабораторной телеметрии использует токен по умолчанию при отсутствии переменной окружения. |
| H-SEC-05 | SECRET | HARD | [PharmacyAuthHeaders.kt:7–10](../clinical-service/src/main/kotlin/com/health/integration/PharmacyAuthHeaders.kt#L7-L10) | Токен фармацевтической интеграции собирается из двух фрагментов в исходном коде. |
| H-SCA-01 | SCA | EASY | [build.gradle.kts:31](../clinical-service/build.gradle.kts#L31) | `com.google.code.gson:gson:2.8.5` — отказ в обслуживании при десериализации циклов (CVE-2022-25647). |
| H-SCA-02 | SCA | EASY | [build.gradle.kts:32](../clinical-service/build.gradle.kts#L32) | `org.apache.pdfbox:pdfbox:2.0.22` — исчерпание памяти при разборе поврежденных PDF (CVE-2021-27807). |
| H-SCA-03 | SCA | MEDIUM | [build.gradle.kts:33](../clinical-service/build.gradle.kts#L33) | `commons-fileupload:commons-fileupload:1.3.2` — уязвимость выполнения кода через DiskFileItem (CVE-2016-1000031). |
| H-SCA-04 | SCA | MEDIUM | [build.gradle.kts:36](../clinical-service/build.gradle.kts#L36) | `org.apache.commons:commons-configuration2:2.7` — RCE через интерполяцию переменных (CVE-2022-33980). |
| H-SCA-05 | SCA | HARD | [build.gradle.kts:38](../clinical-service/build.gradle.kts#L38) | `org.jsoup:jsoup:1.14.1` — DoS при синтаксическом разборе HTML (CVE-2021-37714). |
| H-SAST-01 | SAST | EASY | [LabReportLookupController.kt:13–14](../clinical-service/src/main/kotlin/com/health/records/LabReportLookupController.kt#L13-L14) | Параметр specimenId подставляется в SQL без параметризации. |
| H-SAST-02 | SAST | EASY | [DiagnosticDeviceClient.kt:14](../clinical-service/src/main/kotlin/com/health/integration/DiagnosticDeviceClient.kt#L14) | TLS-клиент медицинского оборудования отключает проверку сертификатов. |
| H-SAST-03 | SAST | MEDIUM | [ClinicalCorsConfig.kt:13–14](../clinical-service/src/main/kotlin/com/health/config/ClinicalCorsConfig.kt#L13-L14) | CORS разрешает origin * с передачей учетных данных для медицинских карт. |
| H-SAST-04 | SAST | MEDIUM | [ClinicalSecurityConfig.kt:18](../clinical-service/src/main/kotlin/com/health/config/ClinicalSecurityConfig.kt#L18) | Экстренная выгрузка медицинских карт доступна публично без аутентификации. |
| H-SAST-05 | SAST | HARD | [PatientRecordReader.kt:10–11](../clinical-service/src/main/kotlin/com/health/records/PatientRecordReader.kt#L10-L11) | Пациент может просмотреть чужую медицинскую карту по ее ID (IDOR). |
