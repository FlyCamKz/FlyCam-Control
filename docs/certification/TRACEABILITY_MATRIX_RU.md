# Матрица прослеживаемости FlyCam

| Требование | Реализация | Автотест/доказательство | Остаточная работа |
|---|---|---|---|
| Несколько БПЛА | `DispatcherClient`, selector/group actions, dispatcher telemetry by `vehicleId` | Windows smoke test IDs 1/2; `test_multiple_vehicles_are_stored_independently` | стенд с уникальными `MAV_SYS_ID` |
| Локальный диспетчер | `dispatcher/server.py`, SQLite, web UI | `dispatcher.test_server` и проверка установленного EXE | резервирование/экспорт по регламенту |
| Защищённый канал | TLS 1.2+, системный CA, optional mTLS | проверки конфигурации; SEC-TLS-01/SEC-MTLS-01 на интеграционном стенде | сертифицированный криптопровайдер/шлюз |
| Минимальные права | viewer/ingest/operator/admin keys | `test_role_scoped_keys_enforce_least_privilege` | выдача, ротация, отзыв и аппаратное хранение |
| Аудит безопасности | `security_audit` SQLite | `test_api_requires_key` | защищённый экспорт в SIEM/WORM |
| Люди/авто на видео | `analytics/video_analytics.py`, ONNX/OpenCV | `analytics.test_video_analytics` | камера, модель и измерение точности |
| Привязка обнаружения к БПЛА | `vehicleId` + последняя телеметрия | `test_detection_batch_round_trip` | оценка допустимой временной рассинхронизации |
| Защита секретов камеры | очистка URL перед журналом/API | `test_source_label_removes_credentials_and_query` | защищённое хранилище конфигурации |
| Управление грузовым отсеком | `CargoBayController`, PX4 actuator command, JSONL audit | исходный код и RELEASE_CHECKLIST | питание, механика, датчик положения, стенд/полёт |
| Формальная сертификация | данный комплект документов | SHA/CI/протоколы | договор и испытания аккредитованной организации |
