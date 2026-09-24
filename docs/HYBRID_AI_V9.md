# Hybrid AI v9 — local-first, selective Google Repair и устойчивый offline fallback

## Основной контракт

```text
Whisper -> язык -> локальный перевод -> quality gate
                                  |         |
                                  |         +-> OK -> сохранить локальный результат
                                  |
                                  +-> suspicious -> Google Repair только этого сегмента
```

Google не является обязательным основным переводчиком. Если локальный результат пригоден, сетевого запроса нет. Если Google временно недоступен, локальный кандидат сохраняется вместо остановки всего видео. Если Whisper уже определил тот же язык, который выбран как целевой (например `ru→ru`), translation provider полностью пропускается и исходный текст сохраняется как identity-result.

## Pass 9 — terminology-aware ASR, subtitles и dialog ducking

- `translation_glossary.json` используется до машинного перевода: source-термины формируют короткий `initial_prompt` Whisper. Для длинных файлов включается `carry_initial_prompt`, поэтому имена, продукты и технические термины не теряют подсказку после первого окна.
- После MT glossary применяется только как targeted cleanup: replacement разрешён, если термин был в source-сегменте и тот же source-токен реально остался в translated text. Это не заменяет обычные слова глобально.
- После успешной сборки рядом с MP4 пишутся source/translated SRT. При Pause Sync каждый таймкод преобразуется в финальную timeline с учётом вставленных пауз.
- При `keep_original` и доступном `sidechaincompress` оригинальная дорожка становится sidechain-ducked под русской речью. При старой/minimal FFmpeg сборке код автоматически возвращается к историческому `amix`.
- Pause Sync использует frame-count `tpad` на ветках, где известен FPS. Это устраняет реальный FFmpeg 7.1 случай, когда duration-based padding после `trim,setpts` не создавал clone frames. FPS нормализуется только на ветках, которым нужен `tpad`, чтобы не навязывать CFR всей VFR-записи.
- Финальная валидация отдельно защищает от тяжело усечённого source video stream.

Upstream evidence: OpenAI Whisper `transcribe.py` (`initial_prompt`, `carry_initial_prompt`, word timestamps/hallucination guard) и официальная FFmpeg filters documentation (`sidechaincompress`, `tpad`).

## Локальные переводчики

- **MarianMT OPUS EN→RU** — приоритетный установленный backend для английского. В v9 несколько сегментов переводятся одним model batch, что уменьшает накладные расходы CPU/GPU.
- **Argos Translate** — универсальный offline backend. Прямой пакет имеет приоритет; если прямого `source→ru` нет, программа может автоматически поставить `source→en` и `en→ru`. Argos штатно умеет строить composite/pivot translation через установленный промежуточный язык.

Автоустановка касается языковых пакетов Argos, а не тяжёлых Python-движков. В source-run недостающий движок можно поставить через GUI/pip. В собранном EXE Python-движки должны быть включены на этапе сборки.

## Selective Google Repair

Quality gate проверяет:

- пустой/слишком короткий ответ;
- почти неизменённый исходник;
- заметную долю английского в EN→RU;
- 2+ обычных английских слова, сохранившихся из исходника внутри русского предложения;
- одно длинное обычное lower-case слово из исходника, оставшееся внутри русского перевода.

Технические термины из allow-list (`API`, `Python`, `Docker`, `FFmpeg`, `GitHub`, `CUDA` и др.) не считаются ошибкой сами по себе. Repair получает целый проблемный сегмент, а не отдельное слово без контекста.

## Argos multilingual route

Порядок подготовки маршрута:

1. если уже есть рабочий route — ничего не скачивать;
2. если в package index есть прямой `source→target` — поставить его;
3. иначе для пары, где обе стороны не English, попробовать `source→en` + `en→target`;
4. после установки проверить реальный `get_translation()`; только затем считать offline route готовым.

## Надёжная загрузка моделей

Большие файлы скачиваются в `*.part`. При обрыве partial file сохраняется. Следующая попытка отправляет HTTP `Range` и продолжает загрузку. Если сервер проигнорировал Range и вернул полный ответ, `.part` перезаписывается с нуля — дописывание полного файла к partial запрещено. HTTP 416 больше не оставляет загрузку в вечном цикле: checksum-подтверждённый полностью скачанный `.part` принимается как готовый, а непроверяемый/устаревший partial безопасно перекачивается с byte 0.

Piper ONNX после установки/явной проверки валидируется SHA-256. В горячем TTS-пути checksum не пересчитывается для каждой фразы: это устраняет повторное чтение десятков мегабайт на каждый сегмент. При этом sidecar JSON проверяется перед выбором голоса, а HTTP resume принимает 206 только с корректным `Content-Range`; сомнительный partial-response не дописывается к модели.

Установленные данные живут в `~/.video_translator_models` (или в `VIDEO_TRANSLATOR_MODELS_DIR`, если пользователь явно перенёс хранилище). Это каталог профиля пользователя, а не каталог конкретной распаковки/версии программы, поэтому новый архив/EXE повторно голоса не скачивает. Для каждой управляемой установки сохраняется `install_manifest.json` с fingerprint совместимого каталога приложения.

При запуске GUI проверяет **только уже установленные** компоненты и не скачивает ничего автоматически. Каждый запуск делает быстрый presence/catalog scan; полная SHA-256/JSON-проверка тяжёлых голосов выполняется не чаще раза в 7 дней, пока состояние здоровое. Смена catalog fingerprint всё равно обнаруживается сразу на каждом запуске. Если глубокая проверка нашла повреждение или завершилась ошибкой, maintenance timestamp не продвигается — следующий запуск снова проверит проблему, а не спрячет её на неделю. Пока идёт проверка, кнопка старта видео временно недоступна. Если обнаружено повреждение или catalog fingerprint/закреплённая версия изменилась, пользователь получает список и выбор «Обновить сейчас?» / отложить. Отказ от обновления не удаляет старые файлы и не мешает начать перевод. Старые установки без manifest перед adoption проходят полную локальную проверку и только затем получают baseline manifest без принудительной повторной загрузки.

Каталог приложения является allow-list совместимости: программа не обновляет AI-зависимости до произвольного upstream `latest`. Для source-run закреплённый Piper pip-пакет можно обновить из менеджера; в PyInstaller EXE engine-code обновляется вместе с новой сборкой программы, тогда как model/voice data остаётся в постоянном store.

### Дополнительная reliability-проходка startup/model manager

- Catalog entry `argos-en-ru` теперь означает именно **прямой** установленный пакет EN→RU. Рабочий pivot route по-прежнему допустим для auto-route, но больше не маскируется в UI как установленный прямой пакет.
- Startup scan разделён на быстрый и глубокий режимы: catalog/version checks — каждый запуск, тяжёлый checksum — периодически.
- HTTP Range resume обрабатывает `416 Range Not Satisfiable` без вечного застревания `.part`.
- Для задач про локальные модели/голоса добавлена отдельная Codex task-card; targeted verifier теперь находит тест-классы во всех `tests/test_*.py`, а не только в историческом diagnostics-файле.

## Piper / TTS

```text
выбранный Edge TTS -> установленные Piper-голоса по очереди -> gTTS fallback
```

Интерактивное ожидание смены VPN отключено. Если Edge недоступен, pipeline не показывает popup и не требует подтверждения: сначала пробует установленные локальные Piper-голоса, затем gTTS. Если конкретный Piper-голос повреждён/не совместим с runtime, он не останавливает задачу — следующий установленный голос пробуется автоматически.

Prepared/raw cache key и metadata используют **фактически выбранный** Piper voice id. Дополнительно cache key содержит быструю ревизию ONNX+JSON (size/mtime), поэтому fallback на другой локальный голос не может ошибочно переиспользовать WAV от предыдущей модели, а после обновления файлов старый persistent TTS cache не подменит новый голос старым звуком.

Управление модельным хранилищем и обработка видео взаимно исключены на уровне GUI: нельзя удалить/обновить ONNX или переводчик в момент, когда pipeline им пользуется.

## EXE и Python-движки

PyInstaller фиксирует Python-модули на этапе сборки. Поэтому внешний `pip install` после запуска готового EXE не делает новый пакет импортируемым внутри уже собранного bundle. `build_exe.bat` заранее устанавливает optional AI runtime и `tools/build_exe.py` явно собирает динамически импортируемые пакеты.

Модельные данные (Marian weights, Argos language packages, Piper voices) в EXE не вшиваются — они сохраняются в профиле пользователя и управляются вкладкой «Переводчики и голоса».

## Verification

- `python tools/verify_project.py`
- `python -m pytest -q`
- отдельные regression tests: `tests/test_hybrid_ai_v8.py`, `tests/test_hybrid_ai_v9.py`

Runtime-only: живой Windows GUI/EXE, реальные модельные загрузки, Edge/Google/VPN, GPU Whisper и длительный FFmpeg pipeline требуют проверки на пользовательском Windows ПК.

## Reliability pass 2026-09-17

- User settings are now written atomically (`temp -> fsync -> os.replace`) and settings I/O failures are structured diagnostic events instead of silent `except: pass`.
- Selective repair is multilingual: Latin-source leftovers are detected for common Latin-script languages; distinct Japanese/Chinese/Korean/Arabic/Greek/Hebrew source-script leftovers in otherwise Russian output also trigger repair.
- MarianMT uses Hugging Face `local_dir` downloads, so a complete second model copy is not retained in the default global Hub cache. Current Hugging Face documentation explicitly states that `local_dir` bypasses the main cache and keeps only local metadata.
- Application-local FFmpeg takes precedence over PATH. The Windows installer/build flow can bootstrap the current Gyan release-essentials pair with SHA-256 verification; PyInstaller includes the exact ffmpeg/ffprobe pair in the artifact.
- Historical source snapshots were removed from `repair_evidence/`; reports and benchmark evidence remain. `pytest.ini` still isolates current tests defensively.
## Дополнительная защита локального контура

Если локальный backend падает с исключением (например, нехватка памяти, повреждённые веса или несовместимость runtime), он помечается недоступным до конца текущего видео. Следующие сегменты сразу переходят к следующему локальному backend или Google Repair вместо повторения одной и той же тяжёлой ошибки сотни раз. Событие `local_translation_backend_disabled` сохраняет backend, тип исключения и recovery action.

Argos-пакеты после установки не оставляют лишний скачанный `.argosmodel` в cache: рабочая распакованная модель остаётся, временный архив удаляется. Перед auto-install программа по возможности получает размер пакета и проверяет свободное место.

## FFmpeg в Windows / PyInstaller

Приоритет разрешения бинарников: bundled resource внутри PyInstaller (`__file__`) → portable `ffmpeg.exe`/`ffprobe.exe` рядом с запущенной программой → системный `PATH`. Это исключает случайное использование старого системного FFmpeg вместо проверенной версии из artifact.

`Установить_зависимости.bat` и `build_exe.bat` вызывают `tools/ensure_ffmpeg_windows.py`: если пары ffmpeg+ffprobe нет, helper может скачать Windows release-essentials ZIP, проверить опубликованный SHA-256 и извлечь только нужные бинарники. Build прекращается, если комплектной пары нет — EXE не считается переносимым только потому, что на машине разработчика FFmpeg найден в PATH.

## Транзакционная защита model store

Piper-голоса и MarianMT больше не обновляются поверх рабочей установки. Для каждого компонента используется совместимый staging-каталог рядом с целевой моделью. Сначала скачиваются/докачиваются все данные, затем выполняется локальная integrity-проверка, и только после этого каталог атомарно переключается через rename. При сетевом сбое staging сохраняется для resume, а старая установка остаётся рабочей.

Staging содержит fingerprint каталога приложения. Если следующая версия программы изменила источник/модель/checksum, старый staging отбрасывается вместо опасного продолжения несовместимого partial. В момент commit предыдущий каталог временно получает суффикс `.previous`; если процесс упал в этом окне, следующий запуск model manager восстанавливает прежнюю установку.

Записывающие операции защищены не только `threading.RLock`, но и lock-файлом всего model store. На Windows используется `msvcrt.locking`, на POSIX — `flock`, поэтому две копии программы не могут одновременно менять одно постоянное хранилище.

Глубокая проверка Marian валидирует все управляемые JSON-файлы (`config`, `generation_config`, `tokenizer_config`, `vocab`). Piper Engine требует одновременно импортируемый модуль и metadata distribution `piper-tts`; Windows build явно включает metadata через PyInstaller `--copy-metadata piper-tts`.

## Crash-safe commit и единый runtime contract

Transaction marker staging-каталога сохраняется вплоть до атомарного `staging -> target` rename. Если процесс оборвался после полной загрузки/валидации, но до commit, следующая попытка видит совместимый fingerprint и продолжает с уже готовых данных вместо повторной загрузки. После успешного commit marker удаляется из активного каталога; если процесс завершился раньше cleanup, startup recovery удалит его позже.

Старый `.previous` после успешного commit является только cleanup-артефактом. Если Windows временно держит старые файлы открытыми и `rmtree` не проходит, новая проверенная модель остаётся активной, операция не выдаёт ложный failure, а cleanup повторяется при следующем старте. При этом перед новым write старый backup по-прежнему должен быть очищен, чтобы не потерять rollback-семантику.

Runtime-проверки приведены к installer contract: Piper требует distribution metadata `piper-tts`, непустой ONNX и корректный JSON sidecar; Marian проверяет тот же набор `config/generation_config/pytorch_model/source.spm/target.spm/tokenizer_config/vocab`, что и manager. Этот набор хранится в единой константе `MARIAN_MODEL_FILES` в catalog, поэтому installer и runtime не могут тихо разойтись после будущего изменения состава модели.

## Актуальный regression gate

- `python tools/verify_project.py` → **199 tests, 17 skipped**, все project gates PASS.
- `python -m pytest -q` → **182 passed, 17 skipped, 19 subtests passed**.

`NOT VERIFIED`: реальный Windows GUI/EXE, фактическое скачивание моделей/голосов, живые Google/Edge/VPN, GPU Whisper и полный многоминутный FFmpeg pipeline — это representative runtime-проверки, которые требуют пользовательского Windows окружения.
