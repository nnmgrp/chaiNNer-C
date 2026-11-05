# ТЗ: нативное аппаратное кодирование NVIDIA NVENC в ноде Save Video

**Статус:** черновик, требуется утверждение перед реализацией  
**Дата:** 2026-11-05  
**Репозиторий:** `F:\Downloads\chaiNNer_C_git` (chaiNNer-C, upstream 0.25.1-nightly, UI v0.3.8)  
**Целевое железо:** NVIDIA RTX 5060 Ti / RTX 5070 Ti, Windows, драйвер 610.88 (проверено на 5060 Ti)

---

## 0. Кратко

Добавить в существующую ноду **Save Video** три аппаратных энкодера NVIDIA:

- `h264_nvenc`
- `hevc_nvenc`
- `av1_nvenc`

Кодирование выполняет тот же встроенный FFmpeg, который уже используется нодой. Никаких внешних скриптов, патчей FFmpeg, DLL и отдельных процессов, кроме самого FFmpeg, не появляется.

Ключевое ограничение: **цветовая конверсия RGB → YUV остаётся на CPU** (libswscale), потому что в закреплённом FFmpeg 9.0.2 `scale_cuda` не умеет RGB → YUV с управляемой матрицей и диапазоном. Это не мешает убрать главное узкое место — кодирование x264/x265 — и даёт ускорение в 3–9 раз на целевой машине. GPU-конверсия цвета вынесена в отдельный этап (Stage 2) и в это ТЗ не входит.

Итог: **Stage 1 = NVENC-кодирование при полностью сохранённом текущем цветовом контракте и текущей атомарной записи файла.**

---

## 1. Цель и критерии успеха

### Цель

Снять CPU-бутылочное горлышко кодирования в Save Video, включив аппаратный NVENC внутри текущей ноды, без изменения пользовательского контракта цвета, аудио и атомарной записи.

### Критерии успеха Stage 1

1. В Advanced-режиме доступны H.264 NVENC, H.265 NVENC и AV1 NVENC для оговорённых контейнеров.
2. Выходной поток до энкодера **побитово тот же**, что у libx264/libx265: тот же `scale` с `out_color_matrix`, `out_range`, `out_h_chr_pos`/`out_v_chr_pos`, `accurate_rnd+full_chroma_int`, тот же `setparams`.
3. Цветовые теги (matrix, primaries, transfer, range, chroma location) присутствуют в файле и совпадают с CPU-путём.
4. Аудио-мукс, `faststart`, атомарная замена файла и обработка ошибок энкодера не меняются.
5. Ускорение на целевой машине: **≥ 4×** на 1080p и **≥ 3×** на 4K относительно libx265 `medium`/CRF 23 при сопоставимом качестве.
6. При отсутствии NVIDIA GPU/драйвера или поддержки кодека — понятная ошибка **до** записи кадров, без тихого отката на CPU.
7. Все существующие цепочки и CPU-энкодеры работают как раньше; старые ID входов не меняются.

### Не-цели Stage 1

- GPU-конверсия цвета (`scale_cuda`, `libplacebo`, NPP).
- Zero-copy из GPU-тензоров в NVENC SDK.
- Аппаратное декодирование в Load Video.
- 4:2:2/4:4:4 и 12/16-бит в UI NVENC.
- Изменение Simple-режима.
- Новые зависимости, новые DLL, отдельные exe.

---

## 2. Что уже проверено на целевой машине

Проверки выполнены закреплённым бинарником `ffmpeg-n9.0.2-17-g2a571b6068-win64-gpl-9.0` из `backend/src/nodes/impl/ffmpeg.py`.

| Проверка | Результат |
|---|---|
| Наличие энкодеров | `h264_nvenc`, `hevc_nvenc`, `av1_nvenc` присутствуют в `-encoders` |
| Сборка | `--enable-ffnvcodec`; NVENC и libplacebo есть, `scale_npp` нет |
| H.264 8-bit | работает |
| H.264 10-bit (High 10) | работает на RTX 5060 Ti (не будет открыто в UI, см. §6) |
| HEVC 8/10-bit | работает |
| AV1 8/10-bit | работает (RTX 50 / Ada) |
| HEVC 4:4:4 и 4:2:2 | работает на 5060 Ti (не будет открыто в UI) |
| H.264 4:4:4 | работает на 5060 Ti (не будет открыто в UI) |
| AV1 4:4:4 | **не работает**: `No capable devices found` |
| Нечётный размер 321×241 | NVENC молча выдал 320×240 — **обязательна проверка чётности** |
| Цветовые теги через `setparams` | matrix/primaries/transfer/range/chroma location доходят до файла |
| `-tune lossless -rc constqp -qp 0` (HEVC) | работает, пригодно для тестов цвета |
| `scale_cuda` RGB→YUV | **не поддерживается** в 9.0.2: `Unsupported conversion: rgb0 -> planar8` |
| `scale_cuda` опции `out_color_matrix`/`out_range` | в 9.0.2 **отсутствуют** (есть только в master после 9.0) |
| Probe-команда NVENC | ~340 мс, пригодна для кэшируемой проверки |

### Замеры FFmpeg-only (не end-to-end ноды)

Машина: Ryzen 5 3600, RTX 5060 Ti, драйвер 610.88. Вход `testsrc2`, 16-bit RGB → 10-bit 4:2:0, вывод `-f null`, без диска и без Python-пейлоада.

| Сценарий | FPS |
|---|---:|
| 1080p, HEVC NVENC p5/cq23, 10-bit | **192** |
| 1080p, libx265 medium/crf23, 10-bit | **21** |
| 4K, только генерация кадра | 260 |
| 4K, генерация + CPU `scale` → yuv420p10le | **76** |
| 4K, генерация + CPU `scale` + HEVC NVENC | **45** |
| 4K, libx265 medium/crf23, 10-bit | **6.4** |

Выводы:

- На 1080p NVENC быстрее x265 medium примерно в **9 раз**.
- На 4K NVENC быстрее примерно в **7 раз**, но после включения NVENC узким местом становится CPU-`scale` (потолок ~76 fps на 4K).
- 4K60 в Stage 1 не гарантируется: end-to-end ~45 fps в идеальных условиях, с Python-пейлоадом будет меньше.

---

## 3. Проблемы и решения

| # | Проблема | Решение в Stage 1 |
|---|---|---|
| 1 | В текущей ноде нет NVENC, только libx264/libx265/VP9/FFV1 | Добавить три значения `VideoEncoder` и провести их через enum, условия UI, native-зеркало |
| 2 | Имена пресетов x264 (`ultrafast`…`veryslow`) NVENC не принимает | Отдельный enum `NvencPreset` со значениями `p1`…`p7`, отдельный вход UI |
| 3 | `-crf` у NVENC нет; качество задаётся `-cq` | Отдельный слайдер CQ (1–51) и `-rc vbr -cq N -b:v 0` |
| 4 | 4:4:4, 4:2:2, 10-bit H.264 и 12-bit зависят от GPU | В UI открыть только гарантированную матрицу: 4:2:0, 8/10-bit; остальное — понятный отказ |
| 5 | Нечётные размеры NVENC молча обрезает | Расширить существующую проверку чётности на все NVENC-энкодеры |
| 6 | Цветовой контракт нельзя терять | Оставить тот же CPU `scale` + `setparams`; NVENC получает уже готовые YUV-планы |
| 7 | HDR10-метаданные (mastering display, MaxCLL) | В CLI-пути NVENC их задать нечем; при HDR в Colour Info — предупреждение, метаданные не пишутся |
| 8 | Лимит одновременных NVENC-сессий у consumer-драйверов | Понятная ошибка при `OpenEncodeSessionEx failed`; без тихого падения и без CPU-fallback |
| 9 | Выход NVENC недетерминирован между GPU/драйверами | Тесты без хэшей: структура, теги, PSNR/SSIM |
| 10 | `scale_cuda` в 9.0.2 не умеет RGB→YUV | Stage 2 отложен до FFmpeg с нужным `scale_cuda`; в Stage 1 CPU-конверсия |
| 11 | Native-зеркало: нода исполняется через C++ граф | Добавить изменения в `native/src/video_io.cpp`, а не только в Python |
| 12 | Тесты вызывают `save_video_node` позиционно | Обновить сигнатуры и все call sites (список в §9.3) |
| 13 | Политика `native/GPU-DEFERRED.md` запрещает GPU-бенчмарки | Запросить разовое исключение на функциональные тесты и приёмочный замер (см. §15) |

---

## 4. Архитектурное решение

### 4.1 Как устроен текущий Save Video

1. Кадр приходит как float32 RGB NumPy.
2. `Writer.payload()` → `to_uint16_gbr_planes()` → `gbrp16le` (три 16-битных плана).
3. Native-писатель запускает встроенный FFmpeg, кадры идут в `stdin` как `rawvideo`.
4. FFmpeg делает `scale` в YUV с явными `out_color_matrix`, `out_range`, `out_h_chr_pos`/`out_v_chr_pos` и `flags=accurate_rnd+full_chroma_int`.
5. `setparams` ставит matrix/primaries/transfer на кадры.
6. Энкодер (`libx264`, `libx265`, `libvpx-vp9`, `ffv1`) пишет временный файл.
7. При успешном закрытии файл атомарно заменяет целевой; при аудио — второй проход мукса.

### 4.2 Stage 1: NVENC без изменения цветового контракта

Меняется только шаг 6: вместо CPU-энкодера выбирается `h264_nvenc` / `hevc_nvenc` / `av1_nvenc`. Шаги 1–5 не меняются.

Это значит:

- Вход NVENC — те же `yuv420p` / `yuv420p10le` планы, что получил бы libx264/libx265.
- Цветовые теги и `setparams` остаются прежними.
- Аудио, `faststart`, атомарная запись, обработка stderr и exit code — без изменений.
- Никакого нового процесса, кроме всё того же FFmpeg.

### 4.3 Почему не zero-copy и не GPU-цвет

- Текущий контракт изображения — CPU NumPy float32. Кадры не живут в VRAM, поэтому «нулевого копирования» из тензора в NVENC SDK в этой архитектуре нет.
- Прямой NVENC SDK потребовал бы держать GPU-поверхности, свой CUDA-контекст и новый контракт пайплайна — это отдельный проект, а не «встроить в текущую ноду».
- `scale_cuda` в FFmpeg 9.0.2 не делает RGB→YUV; проверено: `Unsupported conversion: rgb0 -> planar8`. Опции `out_color_matrix`/`out_range` появились только в master после 9.0.
- `libplacebo` (Vulkan) технически может конвертировать, но меняет математику цвета, добавляет dither и не гарантирует совпадения с текущим контрактом. Для Stage 1 отклоняется.

### 4.4 Stage 2 (отложено, не входит в это ТЗ)

Когда закреплённый FFmpeg получит `scale_cuda` с RGB→YUV и `out_color_matrix`/`out_range`:

- добавить опциональный чекбокс «GPU colour conversion»;
- `hwupload_cuda,scale_cuda=format=p010le:out_color_matrix=...:out_range=...,hwdownload` или прямая подача CUDA-кадра в NVENC;
- включить только после проверки, что PSNR/SSIM и теги совпадают с CPU-путём в пределах допуска;
- отдельный замер: на 4K CPU-`scale` даёт потолок ~76 fps, GPU-конверсия может поднять его, но упрётся в PCIe-аплоад 16-bit RGB (~3 ГБ/с на 4K60).

---

## 5. Пользовательский интерфейс

### 5.1 Новые значения `VideoEncoder`

| Enum | `value` (vcodec) | Подпись |
|---|---|---|
| `H264_NVENC` | `h264_nvenc` | `H.264 (NVENC)` |
| `H265_NVENC` | `hevc_nvenc` | `H.265 (NVENC)` |
| `AV1_NVENC` | `av1_nvenc` | `AV1 (NVENC)` |

Порядок в выпадающем списке: сначала существующие CPU-энкодеры, затем NVENC.

### 5.2 Матрица контейнеров

| Контейнер | CPU | NVENC |
|---|---|---|
| MKV | H.264, H.265, VP9, FFV1 | H.264, H.265, AV1 |
| MP4 | H.264, H.265, VP9 | H.264, H.265, AV1 |
| MOV | H.264, H.265 | H.264, H.265 |
| WEBM | VP9 | AV1 |
| AVI | H.264 | — |
| GIF | — | — |

Причины:

- AV1 в MP4 и WebM проверен на закреплённом FFmpeg.
- MOV оставлен только для H.264/H.265, чтобы не расширять непроверенные комбинации.
- AVI не трогаем: legacy-контейнер, текущая поддержка только libx264.

### 5.3 Пресеты и качество NVENC

Новый enum:

```python
class NvencPreset(Enum):
    P1 = "p1"   # fastest / lowest quality
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"   # NVENC default
    P5 = "p5"   # default in Save Video
    P6 = "p6"
    P7 = "p7"   # slowest / best quality
```

UI: `EnumInput(NvencPreset, label="NVENC Preset", default=NvencPreset.P5)`.

Качество: `SliderInput("CQ", min=1, max=51, default=23, ends=("Best", "Worst"))`.

- CQ — это не CRF. Меньше = лучше.
- `0` у NVENC означает «automatic», поэтому слайдер начинается с 1.
- AV1 принимает 0–63; UI даёт общий диапазон 1–51, более высокие значения — через Additional parameters (`-cq`).

### 5.4 Planes mode

- Разрешено только 4:2:0, 8 или 10 бит (в зависимости от энкодера).
- 4:2:2, 4:4:4, 12/16 бит, RGB-планы — отказ с текстом:
  > `hevc_nvenc writes 4:2:0 at 8 or 10 bits, not yuv444p16le. Choose 4:2:0, a lower Bit Depth, or a CPU encoder (libx265/FFV1).`
- Причина: NVENC на consumer-картах не гарантирует 4:4:4/4:2:2, а молчаливая смена формата нарушила бы контракт Planes mode.

### 5.5 Simple mode

Без изменений. Simple по-прежнему выдаёт CPU H.264/H.265/VP9. Это осознанно: Quality-слайдер Simple откалиброван под CRF, а не CQ; добавление NVENC в Simple — отдельное решение.

### 5.6 Additional parameters

Разрешено переопределять:

- `-gpu N` — выбор GPU;
- `-cq N`, `-rc constqp -qp N`, `-tune lossless`, `-multipass fullres`, `-g N` и т.п.

Запрещены к переопределению (как сейчас): `filename`, `vcodec`, `crf`, `preset`, `c:`.

`-cq` и `-rc` специально не блокируются: пользователь может настроить lossless/constqp, не меняя ноду.

---

## 6. Форматы, битность, пиксельные форматы

### 6.1 Гарантированная матрица (открывается в UI)

| Энкодер | Битности | Пиксельные форматы | Комментарий |
|---|---|---|---|
| `h264_nvenc` | 8 | `yuv420p` | 10-bit H.264 есть на Blackwell, но не гарантирован на других GPU |
| `hevc_nvenc` | 8, 10 | `yuv420p`, `yuv420p10le` | основной вариант для 10-bit |
| `av1_nvenc` | 8, 10 | `yuv420p`, `yuv420p10le` | только RTX 40/50 |

`auto_depth`:

- H.264 NVENC — 8;
- H.265 NVENC — 10;
- AV1 NVENC — 10.

Правила `Bit Depth = Auto` остаются прежними: берётся глубина Colour Info, округляется вверх до поддерживаемой, но не выше `auto_depth`.

### 6.2 Что сознательно не открыто

- H.264 10-bit (High 10) — работает на 5060 Ti, но не гарантировано на Turing/Ampere.
- HEVC/H.264 4:4:4 и 4:2:2 — зависят от GPU.
- 12-bit — NVENC consumer не гарантирует.
- RGB-выход (`gbrp`, `bgr24`) — NVENC не пишет RGB в нашем контракте.

Всё это можно добавить позже отдельным этапом с probe на конкретный формат.

### 6.3 Чётность

Все NVENC-энкодеры требуют чётных width/height для 4:2:0. Существующая проверка в `writer_open` расширяется на `H264_NVENC`, `H265_NVENC`, `AV1_NVENC`. Без неё NVENC молча обрежет кадр (проверено: 321×241 → 320×240).

---

## 7. Точный маппинг параметров FFmpeg

Для NVENC добавляются:

```
-c:v h264_nvenc | hevc_nvenc | av1_nvenc
-preset p1..p7
-tune hq
-rc vbr
-cq <1..51>
-b:v 0
-multipass qres
-spatial-aq 1
-pix_fmt yuv420p | yuv420p10le
```

Почему так:

- `-rc vbr -cq N -b:v 0` — режим постоянного качества; без `-b:v 0` VBR может уйти в битрейт по умолчанию.
- `-tune hq` — качественный тюнинг (значение по умолчанию, фиксируем явно).
- `-multipass qres` — второй проход в четверть разрешения, умеренная цена за качество.
- `-spatial-aq 1` — адаптивное квантование, обычно помогает на градиентах и тексте.
- `-temporal-aq` не включаем по умолчанию: эффект зависит от контента, а поведение должно быть предсказуемым.
- `-rc-lookahead` не включаем: латентность и память; доступно через Additional parameters.
- `-profile` вручную не задаём: FFmpeg выставляет Main/Main10 по `pix_fmt`; проверено, что 10-bit HEVC выходит Main 10.

Всё остальное (цветовые теги, `scale`, `setparams`, `-movflags faststart`, `-r`) остаётся как есть.

---

## 8. Цвет, HDR, аудио, атомарность

### 8.1 Цвет

- Вход энкодера идентичен CPU-пути: тот же `scale` с явной матрицей/диапазоном/позициями хромы и `accurate_rnd+full_chroma_int`.
- `setparams` остаётся последним в цепочке; проверено, что с ним NVENC пишет matrix/primaries/transfer.
- Теги контейнера (`colorspace`, `color_primaries`, `color_trc`, `color_range`, `chroma_sample_location`) остаются прежними.
- Тесты цвета для NVENC используют lossless (`-tune lossless -rc constqp -qp 0`) там, где нужно проверить именно конверсию, и CQ 23 — для визуального качества.

### 8.2 HDR10

- NVENC умеет mastering display / content light level через side data кадра.
- В текущем пайплайне side data нет: rawvideo-пайп их не несёт, а CLI-опции для nvenc отсутствуют.
- Поэтому при `ColourInfo.hdr != None` и NVENC:
  - писать предупреждение в лог: `NVENC does not write HDR10 mastering metadata; use libx265 if you need it.`;
  - не падать, видео сохраняется с color tags, но без mastering display/MaxCLL.
- Это ограничение зафиксировать в `ARCHITECTURE.md` §7.

### 8.3 Аудио и атомарность

Без изменений:

- аудио муксится тем же FFmpeg после видео;
- `faststart` остаётся на муксе;
- временный файл и `os.replace` не трогаются;
- ошибки FFmpeg (exit code, stderr) обрабатываются существующим `EncoderLog`/`raise_if_encoder_stopped`.

---

## 9. Изменения в коде

### 9.1 `backend/src/packages/chaiNNer_standard/image/video_frames/save_video.py`

1. `VideoEncoder`:
   - добавить `H264_NVENC`, `H265_NVENC`, `AV1_NVENC`;
   - свойство `nvenc` (`value.endswith("_nvenc")`);
   - `bit_depths`: H.264 NVENC `(8,)`, H.265/AV1 NVENC `(8, 10)`;
   - `pixel_formats`: для NVENC только `yuv420p` / `yuv420p10le`;
   - `auto_depth`: 8 / 10 / 10.
2. `NvencPreset` (P1–P7), константы качества.
3. `PARAMETERS`: для NVENC `["nvenc_preset", "nvenc_cq"]`; расширить `Literal`.
4. `VideoFormat.encoders`: на macOS отфильтровать NVENC (в macOS-сборках FFmpeg NVENC нет). На Windows/Linux не фильтровать по GPU — проверка будет в runtime.
   Дополнительно: в native `output_options`/`save_video` явно поднимать ошибку, если выбран NVENC-энкодер, которого нет в `container.encoders` (например, цепочка открыта на macOS). Иначе `vcodec` молча не выставится и FFmpeg закодирует libx264 — это недопустимый тихий fallback.
5. `Writer.start`: перед `graph().video_writer_start(...)` вызвать `self._check_nvenc()`; при исключении вызвать `self.target.abandon()` (если target не None), чтобы probe не оставлял временный файл.
6. `Writer._check_nvenc`:
   - если энкодер не NVENC — выход;
   - вычислить целевой pix_fmt (`planar_format(coding)` или `yuv420p`/`yuv420p10le`);
   - вызвать `nvenc_probe(self.ffmpeg_env.ffmpeg, encoder, pix_fmt)`.
7. `nvenc_probe`:
   - кэш успеха по `(ffmpeg_path, encoder.value, pix_fmt)`;
   - ошибки не кэшировать;
   - `subprocess.run(..., capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)`;
   - маппинг stderr в сообщения (§10.2).
8. `Writer._check_pixel_format`: для NVENC — отдельное сообщение про 4:2:0 8/10-bit и отказ на 4:4:4/4:2:2/12/16/RGB.
9. `frame_collector`: добавить `nvenc_preset`, `nvenc_cq` в сигнатуру и в вызов `graph().video_output_options(...)`.
10. `save_video_node`:
    - новые входы после CRF-группы: `EnumInput(NvencPreset)` id **31**, `SliderInput("CQ")` id **32**, внутри `if_enum_group(16, ADVANCED)` и `if_enum_group(3, NVENC_ENCODERS)`;
    - новые `conditions`/`option_labels` для трёх энкодеров;
    - сигнатура: `..., video_preset, crf, nvenc_preset, nvenc_cq, additional_parameters, ...`;
    - передать их в `frame_collector` и `graph().video_save(...)`.
11. `get_simple_format` не менять.

### 9.2 `native/src/video_io.cpp`

1. `container_encoders`:
   - MKV: `H264, H264_NVENC, H265, H265_NVENC, AV1_NVENC, VP9, FFV1`
   - MP4: `H264, H264_NVENC, H265, H265_NVENC, AV1_NVENC, VP9`
   - MOV: `H264, H264_NVENC, H265, H265_NVENC`
   - WEBM: `VP9, AV1_NVENC`
   - AVI/GIF: без изменений.
2. `output_options`:
   - сигнатура `..., preset, crf, nvenc_preset, nvenc_cq, fps, simplicity, additional`;
   - при `PARAMETERS[encoder]` содержит `nvenc_preset`:
     ```
     preset = nvenc_preset.value
     tune = hq
     rc = vbr
     cq = nvenc_cq
     b:v = 0
     multipass = qres
     spatial-aq = 1
     ```
   - Additional parameters применяются после и могут переопределить `cq`, `rc`, `b:v`, `gpu`, `multipass`.
3. `save_video`: сигнатура + проброс в `output_options`; `get_simple_format`-путь не трогать.
4. `writer_open`: проверка чётности для всех NVENC-энкодеров.
5. `writer_frame`, `mux_audio`, `abort_writer`, `EncoderLog` — без изменений.

### 9.3 Тесты и их call sites

Обновить позиционные вызовы `save_video_node` (добавить `nvenc_preset`, `nvenc_cq`):

- `backend/tests/test_save_video_colours.py`
- `backend/tests/test_save_video_atomic.py`
- `backend/tests/test_save_video_audio.py`
- `backend/tests/test_save_video_frame.py`
- `backend/tests/test_video_planes_round_trip.py`

Не менять замороженные копии:

- `native/tests/reference_video/source/.../save_video.py`
- `native/tests/reference_video/installed/.../save_video.py`

Новый файл: `backend/tests/test_save_video_nvenc.py` (§12).

### 9.4 Документация

- `native/ARCHITECTURE.md` §7: новый пункт «Save Video NVENC (approved <дата>)»:
  - какие энкодеры, какие форматы;
  - цветовой контракт сохраняется;
  - HDR10 не пишется;
  - выход недетерминирован, вне frozen-oracle;
  - Stage 2 отложен из-за `scale_cuda`.
- `native/GPU-DEFERRED.md`: строка про NVENC-тесты/замер (см. §15).
- Описание ноды (description) — добавить одну строку про NVENC в Advanced.
- `docs/` — если генератор документации нод подхватывает `.with_docs`, обновить тексты Preset/CQ.

---

## 10. Детект возможностей и ошибки

### 10.1 Probe

Команда (один раз на процесс на комбинацию энкодер+pix_fmt):

```
ffmpeg -hide_banner -loglevel error -nostdin \
  -f lavfi -i color=c=black:s=256x144:r=1 -frames:v 1 \
  -c:v <encoder> -pix_fmt <yuv420p|yuv420p10le> -f null -
```

Свойства:

- ~340 мс, кэшируется;
- ничего не пишет на диск;
- проверяет не только сборку, но и драйвер/GPU/формат;
- при ошибке — исключение до старта записи, временный файл не создаётся;
- при лимите сессий ошибка не кэшируется, следующий запуск пробует снова.

### 10.2 Сообщения об ошибках

| stderr FFmpeg | Сообщение пользователю |
|---|---|
| `Unknown encoder '..._nvenc'` | `This FFmpeg build has no NVENC. Reinstall chaiNNer-C or update FFmpeg.` |
| `Cannot load nvcuda.dll` / `Cannot load libcuda.so.1` | `NVENC needs an NVIDIA driver, but none was found. Install/update the NVIDIA driver or choose a CPU encoder.` |
| `Driver does not support the required nvenc API version` | `The NVIDIA driver is too old for this FFmpeg's NVENC SDK. Update the driver (the message names the required version).` |
| `No capable devices found` | `This GPU has no <codec> NVENC encoder (AV1 needs RTX 40 or newer). Choose H.265 NVENC or a CPU encoder.` |
| `OpenEncodeSessionEx failed` / `out of memory` | `NVIDIA limits concurrent NVENC sessions. Another Save Video may be encoding; serialize the runs or choose a CPU encoder.` |
| `10 bit encode not supported` | `This GPU's <codec> NVENC has no 10-bit support. Choose 8-bit or another encoder.` |
| `Unsupported pixel format` | `NVENC writes 4:2:0 8/10-bit only; <fmt> is not supported.` |
| иное | `NVENC failed to start: <tail of stderr>` |

Все сообщения — через `ValueError`/`RuntimeError` с сохранением исходного stderr, чтобы не терять диагностику.

---

## 11. Совместимость GPU и драйверов

| Требование | Значение |
|---|---|
| ОС | Windows / Linux; macOS — NVENC нет |
| GPU | NVIDIA с NVENC: Kepler+ для H.264, Maxwell 2nd gen+ для HEVC, Ada/RTX 40+ для AV1 |
| Драйвер | поддерживающий NVENC SDK закреплённого FFmpeg (SDK 13.x, ожидаемо R570+); проверено на 610.88 |
| FFmpeg | закреплённый BtbN 9.0.2 GPL; NVENC присутствует |
| Несколько GPU | по умолчанию первый NVENC-совместимый; выбор через `-gpu N` в Additional parameters |
| Несколько нод параллельно | ограничение сессий драйвера; ошибка с подсказкой, без тихого fallback |

RTX 5060 Ti и 5070 Ti полностью покрыты матрицей Stage 1 (H.264 8-bit, HEVC 8/10-bit, AV1 8/10-bit, 4:2:0).

---

## 12. Тесты

### 12.1 Политика

- NVENC недетерминирован: **никаких хэшей и bit-exact сравнений**.
- Для цвета — structural-проверки (теги, pix_fmt, profile) + PSNR/SSIM.
- Функциональные тесты пропускаются, если probe не проходит (`pytest.skip` с причиной).
- В CI GPU-бенчмарки не запускаются.

### 12.2 Новый `backend/tests/test_save_video_nvenc.py`

1. `test_nvenc_encoder_lists` — матрица контейнеров и порядок.
2. `test_nvenc_8bit_h264_round_trip` — H.264 NVENC, BT.709, теги, PSNR.
3. `test_nvenc_10bit_hevc_round_trip` — HEVC NVENC, `yuv420p10le`, теги, PSNR.
4. `test_nvenc_av1_mp4_and_webm` — AV1 NVENC в MP4 и WebM.
5. `test_nvenc_refuses_444_422_12bit` — в Planes mode понятные `ValueError`.
6. `test_nvenc_even_dimensions` — нечётный размер даёт то же сообщение, что CPU-энкодеры.
7. `test_nvenc_probe_cached` — второй запуск не вызывает subprocess.
8. `test_nvenc_probe_errors` — mock stderr для каждого шаблона из §10.2.
9. `test_nvenc_no_silent_fallback` — при провале probe нет выходного файла и нет CPU-энкода.
10. `test_nvenc_lossless_colour` — HEVC lossless, теги и ошибка цвета в пределах допуска.

### 12.3 Существующие тесты

- `test_save_video_colours.py`: добавить параметризацию NVENC (skip без GPU) для проверки четырёх тегов.
- `test_save_video_atomic.py`: один NVENC-кейс — временный файл и `os.replace` не изменились.
- `test_save_video_audio.py`: один NVENC-кейс с аудио.
- `native/tests/test_video_io.py`: structural-тест `container_encoders`; старые oracle-тесты не трогать.
- `native/tests/test_video_io.py` строка ~2718 вызывает `video_writer_start(g, w, 2, 2)` — проверить, что сигнатура не изменилась.

### 12.4 Приёмочные метрики качества

- 10-bit HEVC CQ 23: SSIM ≥ 0.98 относительно исходных кадров на тестовом клипе.
- Сравнение с libx265 CRF 23 при битрейте в пределах ±15 %: SSIM не хуже более чем на 0.01.
- Цвет: те же четыре тега + chroma location; PSNR lossless-пути ≥ 45 dB (практически ограничен только 10-bit квантованием).

---

## 13. Производительность

### 13.1 Методика

- 1080p: 300 кадров `testsrc2`, 10-bit HEVC, p5/cq23.
- 4K: 120 кадров, тот же профиль.
- 3 прогона, медиана, прогрев, один и тот же вход.
- Сравнение: NVENC vs libx265 medium/CRF 23 на идентичных кадрах.
- End-to-end — через ноду, с `payload()` и пайпом; FFmpeg-only замеры из §2 — ориентир.

### 13.2 Приёмка на RTX 5060 Ti

| Метрика | Порог |
|---|---|
| 1080p HEVC 10-bit, end-to-end | ≥ 60 fps и ≥ 4× к x265 medium |
| 4K HEVC 10-bit, end-to-end | ≥ 20 fps и ≥ 3× к x265 medium |
| Потребление CPU | загрузка не выше, чем у x265-пути (энкод снят с CPU) |
| Деградация CPU-путей | 0 (код не меняется) |

4K60 в Stage 1 не гарантируется. Если после реализации 4K end-to-end < 20 fps, это означает, что доминирует Python-пейлоад/`scale`; тогда Stage 2 или оптимизация `payload()` — отдельное решение.

---

## 14. Риски и меры

| Риск | Вероятность | Мера |
|---|---|---|
| Нет NVIDIA GPU/драйвера | средняя | probe + понятная ошибка; без fallback |
| Старый драйвер | средняя | сообщение с требованием версии; документация |
| Лимит NVENC-сессий | средняя | сообщение, рекомендация сериализовать; без падения |
| 4:4:4/12-bit ожидания пользователей | высокая | явный отказ и подсказка на CPU-энкодер |
| Потеря HDR10-метаданных | средняя | предупреждение; документация |
| Недетерминизм выхода | высокая | тесты без хэшей |
| Расхождение цвета | низкая | тот же `scale`/`setparams`; тесты тегов и PSNR |
| Проблемы совместимости AV1 в MP4/WebM | средняя | подпись/документация; AV1 не обязателен |
| Старые цепочки ломаются | низкая | ID не меняются, значения добавляются |
| Probe замедляет старт | низкая | кэш; ~340 мс один раз на процесс |
| Несколько GPU | низкая | по умолчанию первый; `-gpu N` |
| Обновление FFmpeg ломает опции | низкая | закреплённая версия; тесты при обновлении |
| Политика GPU-DEFERRED | средняя | разовое исключение (§15) |

---

## 15. Политика GPU и раскатка

`native/GPU-DEFERRED.md` (2026-10-21) ограничивает GPU-работы: не более 1 ГБ VRAM, без бенчмарков без разрешения.

Для этого ТЗ запрашивается:

1. **Функциональные тесты NVENC** — разрешить (VRAM < 100 МБ на сессию, 1 кадр 256×144 в probe).
2. **Приёмочный замер** — разово, 50 итераций на сторону, с фиксацией результата в отчёте; в CI не включать.
3. После проверки — добавить строку в `GPU-DEFERRED.md` и закрыть её по факту.

Раскатка:

- NVENC — **явный выбор пользователя**, не default. Существующие цепочки не меняют поведение.
- Simple-режим не трогаем.
- При отсутствии GPU пользователь видит ошибку и выбирает CPU-энкодер.
- Откат — удаление новых значений enum и ветки в `output_options`; файлы, записанные CPU-путём, не затрагиваются.

---

## 16. Решения, которые нужно утвердить

| # | Решение | Предлагаю | Альтернатива |
|---|---|---|---|
| D1 | Объём | Stage 1: только NVENC-энкод, CPU-цвет | Сразу GPU-цвет (блокируется FFmpeg 9.0.2) |
| D2 | UI качества | Отдельные `NvencPreset` P1–P7 и CQ 1–51 | Переиспользовать Preset/CRF с маппингом (хуже UX) |
| D3 | Форматы | Только 4:2:0, 8/10-bit | Открыть 4:4:4/10-bit H.264 с probe (сложнее, нестабильно) |
| D4 | Fallback | Нет тихого отката на CPU | Автоматический fallback (скрывает проблемы) |
| D5 | Simple mode | Не менять | Добавить NVENC в Simple |
| D6 | Контейнеры AV1 | MP4 + WebM | Только MKV |
| D7 | HDR10 | Предупреждение, без метаданных | Не поддерживать NVENC при HDR (жёсткий отказ) |
| D8 | macOS | Скрыть NVENC | Показывать с ошибкой |
| D9 | Probe | Один раз на процесс, кэш успеха | Проверка на каждый файл (медленнее) |
| D10 | Тесты | Без хэшей, PSNR/SSIM + structural | Добавить NVENC в frozen-oracle (некорректно) |

---

## 17. Приложение: проверенные команды

Наличие энкодеров:

```
ffmpeg -hide_banner -encoders | findstr nvenc
```

Проверка HEVC 10-bit:

```
ffmpeg -f lavfi -i testsrc2=s=1920x1080:r=30:d=1 ^
  -c:v hevc_nvenc -preset p5 -tune hq -rc vbr -cq 23 -b:v 0 ^
  -pix_fmt p010le -y out.mp4
```

Цветовые теги:

```
ffmpeg -f lavfi -i testsrc2=s=320x240:r=25:d=1 ^
  -vf "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709" ^
  -c:v hevc_nvenc -preset p5 -pix_fmt p010le ^
  -colorspace bt709 -color_primaries bt709 -color_trc bt709 -color_range tv ^
  -y tags.mp4
ffprobe -v error -select_streams v:0 ^
  -show_entries stream=color_space,color_primaries,color_transfer,color_range,chroma_location ^
  -of default=nw=1 tags.mp4
```

Lossless для тестов цвета:

```
ffmpeg ... -c:v hevc_nvenc -preset p5 -tune lossless -rc constqp -qp 0 ...
```

Проверка, что `scale_cuda` не умеет RGB→YUV в 9.0.2:

```
ffmpeg -f lavfi -i testsrc2=s=640x360:r=25:d=1 ^
  -vf "format=gbrp,hwupload_cuda,scale_cuda=format=yuv420p,hwdownload,format=yuv420p" ^
  -f null -
```

Ожидаемо: `Unsupported conversion: rgb0 -> planar8`.

---

**Итог:** Stage 1 даёт аппаратное NVENC-кодирование внутри текущей ноды, сохраняет цветовой контракт, атомарность и аудио, не добавляет внешних зависимостей и ускоряет сохранение в 3–9 раз на целевой машине. GPU-цвет и zero-copy сознательно отложены до появления подходящего FFmpeg и отдельного GPU-пайплайна.
