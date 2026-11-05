# ТЗ для GitHub Copilot: сборка NVENC Stage 1 в форке nnmgrp/chaiNNer-C и интеграция в приложение

## 1. Цель и контекст сборки

Собрать на GitHub Actions нативную часть chaiNNer-C с реализованным аппаратным кодированием **NVIDIA NVENC (Stage 1)** в рамках **нашего форка**:

* **Репозиторий форка:** `https://github.com/nnmgrp/chaiNNer-C.git`
* **Базовая ветка:** `chaiNNer-C` (синхронизирована с апстримом Rift73 v0.3.9, коммит `0e19600e`)
* **Ветка для сборки:** `nvenc-stage1` (содержит изменения NVENC Stage 1)
* **Целевое портативное приложение:** `F:\Downloads\chaiNNer-C-v0.3.4-win-x64`

### Артефакты сборки
1. `_chainner_graph.pyd` — **обязательно** (в него скомпилирован `native/src/video_io.cpp` с матрицей параметров NVENC);
2. `chainner_native.dll` — из той же сборки (линкуется модулем графа);
3. `save_video.py` — Python-узел ноды Save Video с поддержкой энкодеров NVENC, пресетов P1–P7 и CQ.

Фронтенд пересобирать не нужно: схема узлов читается бэкендом из Python при запуске.  
Stage 2 (GPU-конвертация цвета через CUDA/libplacebo) в это ТЗ не входит.

---

## 2. Архитектура и специфика нашего форка

Форк `nnmgrp/chaiNNer-C` объединяет актуальную кодовую базу апстрима и собственные доработки, которые **не должны быть нарушены** при сборке и интеграции:

1. **Синхронизация с v0.3.9 (Rift73):**
   * Electron 45, spandrel c13, CPU BF16 autocast (`use_bf16`);
   * Ускоренный PNG (zlib-ng / libdeflate / libpng с AVX2 unfilter);
   * Оптимизации ESRGAN, HAT, DAT, мультиканальные фиксы;
   * Поддержка ROCm 7.14 / MIGraphX 2.16 и Linux x86-64.
2. **Собственные компоненты форка (сохраняются в неприкосновенности):**
   * **TensorRT FFT Engine Builder**: узел `build_fft_engine.py`, изолированный child-воркер для безопасного экспорта ONNX на GPU без падений Sanic, `cuda_dll.py`, плагины TRT 11.4 `trt_dft_plugins.dll` (`Rfft`/`Irfft`);
   * **Smart Architecture-Aware Tiling & Overlap**: автоматический расчет сетки и безопасного наложения тайлов (`recommended_overlap` 48px для SwinUNet и Attention-моделей) в `backend/src/nodes/impl/pytorch/auto_split.py` и `backend/src/packages/chaiNNer_pytorch/pytorch/processing/upscale_image.py`;
   * **Собственный источник обновлений**: эндпоинт проверки релизов направлен на `nnmgrp/chaiNNer-C` в `src/renderer/helpers/github.ts`, `src/main/gui/menu.ts` и бандлах приложения, предотвращая появление уведомлений об обновлениях апстрима.

---

## 3. Что уже реализовано в ветке `nvenc-stage1`

В рабочей ветке `nvenc-stage1` полностью реализован Stage 1:

| Файл | Что реализовано |
|---|---|
| `backend/src/packages/chaiNNer_standard/image/video_frames/save_video.py` | NVENC-энкодеры (`h264_nvenc`, `hevc_nvenc`, `av1_nvenc`), пресеты P1–P7, CQ 1–51, probe энкодеров, отказ от silent fallback на CPU, предупреждение HDR10, фильтрация под macOS |
| `native/src/video_io.cpp` | Матрица форматов и энкодеров, передача пресетов и CQ в FFmpeg, новые сигнатуры `video_output_options`, валидация чётных габаритов кадра для NVENC |
| `native/tests/test_video_io.py` | Оракульные тесты под новые нативные сигнатуры |
| `backend/tests/test_save_video_nvenc.py` | Тесты NVENC: валидация параметров, probe, исключение ошибок |
| `backend/tests/test_save_video_*.py` | Обновлены вызовы тестов с учетом новых аргументов |
| `native/ARCHITECTURE.md`, `native/GPU-DEFERRED.md` | Документация архитектуры нативного модуля и отложенного Stage 2 |
| `native/NVENC-SPEC.md` | Утверждённая детальная спецификация NVENC Stage 1 |

### Жесткий контракт поведения (менять запрещено):
- Цветопередача: BT.601 / BT.709 по размеру, диапазон `tv`, `setparams`, chroma siting — идентично CPU-пути;
- Аудио-мукс, `faststart` (moov перед mdat), атомарная замена файла через временный `*.tmp`;
- Simple mode и ID существующих входов неизменны; добавлены только вход 31 (`NVENC Preset`) и 32 (`CQ`);
- При ошибке probe или энкодера — явное исключение, **никакого молчаливого перехода на CPU**;
- NVENC скрыт на macOS, доступен на Windows/Linux;
- HDR10 metadata NVENC не записывает — только логирует предупреждение.

---

## 4. Предусловия: подготовка ветки в репозитории форка

Код должен быть отправлен в ветку `nvenc-stage1` репозитория `https://github.com/nnmgrp/chaiNNer-C.git`.

Команды отправки из локального репозитория:

```powershell
cd F:\Downloads\chaiNNer_C_git
git checkout -b nvenc-stage1
git add -A
git commit -m "feat(native): add NVENC video encoding support to Save Video (Stage 1)"
git push -u origin nvenc-stage1
```

### Проверочный чеклист для Copilot перед сборкой
Copilot должен убедиться, что в ветке `nvenc-stage1` присутствуют:
- `native/NVENC-SPEC.md`
- `backend/tests/test_save_video_nvenc.py`
- `VideoEncoder.H264_NVENC / H265_NVENC / AV1_NVENC` в `backend/src/packages/chaiNNer_standard/image/video_frames/save_video.py`
- `nvenc_preset` и `nvenc_cq` в `native/src/video_io.cpp`
- Слияние с v0.3.9 и фичи форка (TensorRT FFT, smart tiling) в базовой истории коммитов.

Если чего-то нет — остановиться и запросить синхронизацию, не дописывать код по памяти.

---

## 5. Артефакты сборки и согласованность бинарников

`native/src/video_io.cpp` компилируется в pybind11-модуль `_chainner_graph.pyd` (target `_chainner_graph`), а не в `chainner_native.dll`.

* `_chainner_graph.pyd` содержит новый код видео-энкодинга NVENC и обновленные сигнатуры.
* `chainner_native.dll` линкуется модулем `_chainner_graph.pyd`. **Оба бинарника должны быть взяты из одной сборки**.
* `save_video.py` и `_chainner_graph.pyd` заменяются строго синхронно: старый Python-код с новым `.pyd` (или наоборот) приведёт к ошибке несоответствия параметров `TypeError`.
* Бинарники компилируются в `backend/src/nodes/impl/` и в git не коммитятся.

> **Примечание по форку:** Сборка `_chainner_graph.pyd` и `chainner_native.dll` из актуального дерева форка объединяет как поддержку NVENC, так и ускорения операций из апстрима v0.3.9. Кастомные модули форка (`trt_dft_plugins.dll`, `build_fft_engine.py`, `cuda_dll.py`) изолированы в пакете `packages/chaiNNer_tensorrt/` и не конфликтуют с модулем графа.

---

## 6. Сборка на GitHub Actions (`.github/workflows/native-nvenc.yml`)

### 6.1. Требования к toolchain (пиннинг неизменен)

`native/toolchain.cmake` и `native/CMakeLists.txt` жёстко фиксируют окружение:
* **LLVM 23.1.2** (clang-cl, lld-link, llvm-lib, llvm-rc);
* **MSVC toolset 14.44.35207**;
* **Windows SDK 10.0.26100.0**;
* **CMake ≥ 3.20, Ninja**;
* **NASM 3.01**;
* **CPython 3.14.8** (устанавливается скриптом `native/tools/provision_runtime.ps1 -BuildOnly`).

**Запрещено:**
- Ослаблять версии компилятора или SDK в `toolchain.cmake` / `CMakeLists.txt`;
- Использовать системный Python runner вместо CPython 3.14.8;
- Использовать флаг `CHAINNER_C_MARCH` для релизной сборки.

### 6.2. Workflow файл `.github/workflows/native-nvenc.yml`

Copilot создает или проверяет рабочий workflow в репозитории `nnmgrp/chaiNNer-C`:

```yaml
name: native-nvenc

on:
  workflow_dispatch:
  push:
    branches: [nvenc-stage1]

permissions:
  contents: read

jobs:
  build:
    runs-on: windows-2022
    timeout-minutes: 180

    steps:
      - uses: actions/checkout@v4

      - name: Show branch state
        shell: pwsh
        run: |
          git log -2 --oneline
          git status --short

      - name: Setup MSVC 14.44 and Windows SDK
        shell: pwsh
        run: |
          # 1. Locate MSVC in pre-installed Visual Studio 2022 Enterprise on windows-2022
          $vsRoot = 'C:\Program Files\Microsoft Visual Studio\2022\Enterprise'
          if (-not (Test-Path $vsRoot)) {
            $vsRoot = (Get-ChildItem 'C:\Program Files\Microsoft Visual Studio\2022' -Directory | Select-Object -First 1).FullName
          }
          $msvcBase = Join-Path $vsRoot 'VC\Tools\MSVC'
          Write-Host "Searching in $msvcBase..."
          $available = Get-ChildItem $msvcBase -Directory
          Write-Host "Available MSVC toolsets: $($available.Name -join ', ')"

          $targetVersion = '14.44.35207'
          $exactMsvc = Join-Path $msvcBase $targetVersion
          if (Test-Path $exactMsvc) {
            $msvc = $exactMsvc
          } else {
            $candidate = ($available | Where-Object { $_.Name -like '14.44.*' } | Sort-Object Name -Descending | Select-Object -First 1)
            if (-not $candidate) {
              $candidate = ($available | Sort-Object Name -Descending | Select-Object -First 1)
            }
            Write-Host "Mapping $($candidate.FullName) to target $targetVersion"
            New-Item -ItemType Directory -Force 'C:\Tools\MSVC' | Out-Null
            $msvc = Join-Path 'C:\Tools\MSVC' $targetVersion
            cmd /c mklink /J $msvc $candidate.FullName
          }
          Write-Host "Resolved MSVC_DIR: $msvc"

          # 2. Locate Windows SDK 10.0.26100.0
          $sdk = 'C:\Program Files (x86)\Windows Kits\10'
          if (-not (Test-Path (Join-Path $sdk 'Include\10.0.26100.0'))) {
            throw "Windows SDK 10.0.26100.0 not found under $sdk"
          }

          # 3. Locate Ninja
          $ninja = (Get-Command ninja -ErrorAction SilentlyContinue).Source
          if (-not $ninja) {
            $ninja = (Get-ChildItem $vsRoot -Recurse -Filter ninja.exe | Select-Object -First 1).FullName
          }
          Write-Host "Resolved Ninja: $ninja"

          "MSVC_DIR=$msvc"   >> $env:GITHUB_ENV
          "SDK_DIR=$sdk"     >> $env:GITHUB_ENV
          "NINJA=$ninja"     >> $env:GITHUB_ENV

      - name: Install LLVM 23.1.2
        shell: pwsh
        run: |
          $llvm = 'C:\Executables\clang+llvm-23.1.2-x86_64-pc-windows-msvc'
          $url = 'https://github.com/llvm/llvm-project/releases/download/llvmorg-23.1.2/clang+llvm-23.1.2-x86_64-pc-windows-msvc.tar.xz'
          Invoke-WebRequest -Uri $url -OutFile C:\Executables\llvm.tar.xz
          tar -xf C:\Executables\llvm.tar.xz -C C:\Executables
          if (-not (Test-Path "$llvm\bin\clang-cl.exe")) { throw "clang-cl not found after extraction" }
          & "$llvm\bin\clang-cl.exe" --version
          "LLVM_DIR=$llvm" >> $env:GITHUB_ENV

      - name: Install NASM 3.01
        shell: pwsh
        run: |
          $zip = 'C:\Executables\nasm.zip'
          Invoke-WebRequest 'https://www.nasm.us/pub/nasm/releasebuilds/3.01/win64/nasm-3.01-win64.zip' -OutFile $zip
          Expand-Archive $zip -DestinationPath C:\Executables -Force
          $nasm = (Get-ChildItem C:\Executables -Recurse -Filter nasm.exe | Select-Object -First 1).FullName
          if (-not $nasm) { throw "nasm.exe not found" }
          & $nasm -v
          "NASM=$nasm" >> $env:GITHUB_ENV

      - name: Provision build Python
        shell: pwsh
        run: powershell -NoProfile -File native\tools\provision_runtime.ps1 -BuildOnly

      - name: Configure and build
        shell: pwsh
        run: |
          & '.\native\Build.ps1' -Configuration Release `
            -Ninja $env:NINJA `
            -Define "chainner_c_llvm=$env:LLVM_DIR\bin", `
                    "chainner_c_vc_tools=$env:MSVC_DIR", `
                    "chainner_c_sdk=$env:SDK_DIR", `
                    "CHAINNER_C_NASM=$env:NASM"
          if ($LASTEXITCODE -ne 0) { throw "Build failed ($LASTEXITCODE)" }

      - name: Check artifacts
        shell: pwsh
        run: |
          $impl = 'backend\src\nodes\impl'
          foreach ($f in '_chainner_graph.pyd', 'chainner_native.dll') {
            $p = Join-Path $impl $f
            if (-not (Test-Path $p)) { throw "Missing $p" }
            Get-FileHash $p -Algorithm SHA256 | Format-List
            Get-Item $p | Select-Object FullName, Length, LastWriteTime
          }

      - name: Smoke-test the new module
        shell: pwsh
        run: |
          $py = '.\native\runtime\cpython-3.14.8\python.exe'
          & $py -B -c @"
          import sys
          from enum import Enum
          sys.path.insert(0, r'backend/src')
          import nodes.impl._chainner_graph as mod
          assert mod.abi_version == 1
          class VideoFormat(Enum):
              MKV='mkv'; MP4='mp4'; MOV='mov'; WEBM='webm'; AVI='avi'; GIF='gif'
          class VideoEncoder(Enum):
              H264='libx264'; H264_NVENC='h264_nvenc'; H265='libx265'
              H265_NVENC='hevc_nvenc'; AV1_NVENC='av1_nvenc'; VP9='libvpx-vp9'; FFV1='ffv1'
          g = {'VideoFormat': VideoFormat, 'VideoEncoder': VideoEncoder}
          mkv = [e.value for e in mod.video_container_encoders(g, VideoFormat.MKV)]
          webm = [e.value for e in mod.video_container_encoders(g, VideoFormat.WEBM)]
          avi = [e.value for e in mod.video_container_encoders(g, VideoFormat.AVI)]
          print('MKV', mkv); print('WEBM', webm); print('AVI', avi)
          assert {'h264_nvenc', 'hevc_nvenc', 'av1_nvenc'} <= set(mkv)
          assert webm == ['libvpx-vp9', 'av1_nvenc']
          assert avi == ['libx264']
          print('NVENC module OK')
          "@

      - name: Upload native artifacts
        uses: actions/upload-artifact@v4
        with:
          name: chainner-native-nvenc
          path: |
            backend/src/nodes/impl/_chainner_graph.pyd
            backend/src/nodes/impl/chainner_native.dll
          if-no-files-found: error
```

---

## 7. Проверки и тестовый гейт

### 7.1. Обязательные быстрые проверки (линтер и форматирование)
```powershell
& '.\native\runtime\cpython-3.14.8\python.exe' -m pip install ruff==0.16.10 pytest
& '.\native\runtime\cpython-3.14.8\python.exe' -m ruff format backend --check
& '.\native\runtime\cpython-3.14.8\python.exe' -m ruff check backend
& '.\native\runtime\cpython-3.14.8\python.exe' -m ruff check native/tests
```

### 7.2. Прогон тестов видео-энкодинга
На runner без аппаратного NVIDIA GPU тесты, запускающие фактическое кодирование NVENC, будут безопасно пропущены (`skipped`), что является нормой.
Обязательно проверяется логика матрицы и генерация команд FFmpeg:
```powershell
& '.\native\runtime\cpython-3.14.8\python.exe' -B -m pytest native/tests/test_video_io.py backend/tests/test_save_video_nvenc.py -q -p no:cacheprovider
```

---

## 8. Интеграция собранных артефактов в портативное приложение

Когда GitHub Actions завершит сборку в репозитории `nnmgrp/chaiNNer-C`, локальный агент или владелец машины скачивает артефакты и интегрирует их в установленное приложение:

```powershell
# 1. Скачать артефакт через GitHub CLI (или из веб-интерфейса Actions):
# gh run download <RUN_ID> -R nnmgrp/chaiNNer-C -n chainner-native-nvenc -D C:\Temp\nvenc-artifacts
```

Скрипт интеграции в `F:\Downloads\chaiNNer-C-v0.3.4-win-x64` (приложение должно быть закрыто):

```powershell
$pkg   = 'F:\Downloads\chaiNNer-C-v0.3.4-win-x64'
$repo  = 'F:\Downloads\chaiNNer_C_git'
$art   = 'C:\Temp\nvenc-artifacts' # путь к скачанным .pyd и .dll

# 0. Создание резервной копии
$stamp = 'bak-' + (Get-Date -Format 'yyyyMMdd_HHmmss')
Copy-Item "$pkg\resources\src\nodes\impl\_chainner_graph.pyd"   "$pkg\resources\src\nodes\impl\_chainner_graph.pyd.$stamp"
Copy-Item "$pkg\resources\src\nodes\impl\chainner_native.dll"    "$pkg\resources\src\nodes\impl\chainner_native.dll.$stamp"
Copy-Item "$pkg\resources\src\packages\chaiNNer_standard\image\video_frames\save_video.py" `
          "$pkg\resources\src\packages\chaiNNer_standard\image\video_frames\save_video.py.$stamp"

# 1. Копирование собранных нативных бинарников
Copy-Item "$art\_chainner_graph.pyd"  "$pkg\resources\src\nodes\impl\" -Force
Copy-Item "$art\chainner_native.dll"   "$pkg\resources\src\nodes\impl\" -Force

# 2. Копирование Python-узла из ветки nvenc-stage1
Copy-Item "$repo\backend\src\packages\chaiNNer_standard\image\video_frames\save_video.py" `
          "$pkg\resources\src\packages\chaiNNer_standard\image\video_frames\save_video.py" -Force

# 3. Очистка кэша байткода Python
Remove-Item -Recurse -Force "$pkg\resources\src\nodes\impl\__pycache__" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$pkg\resources\src\packages\chaiNNer_standard\image\video_frames\__pycache__" -ErrorAction SilentlyContinue
```

> **Важно:** Все специфичные для нашего форка файлы (`build_fft_engine.py`, `cuda_dll.py`, `trt_dft_plugins.dll`, настройки репозитория `nnmgrp/chaiNNer-C` в `index.js`/`main.js`) остаются нетронутыми.

### 8.1. Smoke-тест на Python установленного приложения
```powershell
$pkgPy = 'F:\Downloads\chaiNNer-C-v0.3.4-win-x64\python\python\python.exe'
& $pkgPy -B -c @"
import sys
sys.path.insert(0, r'F:\Downloads\chaiNNer-C-v0.3.4-win-x64\resources\src')
import nodes.impl._chainner_graph as g
assert g.abi_version == 1, g.abi_version
from packages.chaiNNer_standard.image.video_frames.save_video import VideoEncoder, VideoFormat
names = [e.value for e in VideoFormat.MKV.encoders]
print('MKV encoders:', names)
assert {'h264_nvenc', 'hevc_nvenc', 'av1_nvenc'} <= set(names)
assert [e.value for e in VideoFormat.WEBM.encoders] == ['libvpx-vp9', 'av1_nvenc']
assert all(not e.nvenc for e in VideoFormat.AVI.encoders)
print('Smoke-test PASSED successfully!')
"@
```

### 8.2. Проверка поддержки NVENC во встроенном FFmpeg
```powershell
$ff = (Get-ChildItem 'F:\Downloads\chaiNNer-C-v0.3.4-win-x64\backend-storage' -Recurse -Filter ffmpeg.exe | Select-Object -First 1).FullName
& $ff -hide_banner -encoders | Select-String 'nvenc'
```
Ожидается: строки `h264_nvenc`, `hevc_nvenc`, `av1_nvenc`.

---

## 9. Ручная валидация в UI (на RTX 5060 Ti)

1. Запустить `chaiNNer.exe`.
2. Добавить узел **Save Video** (Advanced mode).
3. Проверить выпадающий список энкодеров:
   * **MKV / MP4**: H.264, **H.264 (NVENC)**, H.265, **H.265 (NVENC)**, **AV1 (NVENC)**, VP9 (+ FFV1 для MKV);
   * **MOV**: H.264, H.264 (NVENC), H.265, H.265 (NVENC);
   * **WEBM**: VP9, **AV1 (NVENC)**;
   * **AVI / GIF**: энкодеры NVENC отсутствуют.
4. Проверить входы:
   * Вход 31 (`NVENC Preset`: P1–P7, по умолчанию P5) и вход 32 (`CQ`: 1–51, по умолчанию 23) активны только при выборе энкодера NVENC.
5. Провести пробный рендер короткого тестового видео:
   * H.264 NVENC, MP4, 1080p -> файл создается без ошибок;
   * Проверить через `ffprobe`: `moov` расположен перед `mdat` (работает `+faststart`), диапазон `tv`, `bt709`.
   * Попытка кодирования с нечётным разрешением (например, 1921x1080) вызывает понятную валидационную ошибку до запуска кодирования.

---

## 10. План отката

В случае любой несовместимости:
```powershell
$pkg = 'F:\Downloads\chaiNNer-C-v0.3.4-win-x64'
# Восстановить из созданной резервной копии:
$bak = (Get-ChildItem "$pkg\resources\src\nodes\impl\_chainner_graph.pyd.bak-*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).Extension.TrimStart('.')
Copy-Item "$pkg\resources\src\nodes\impl\_chainner_graph.pyd.$bak" "$pkg\resources\src\nodes\impl\_chainner_graph.pyd" -Force
Copy-Item "$pkg\resources\src\nodes\impl\chainner_native.dll.$bak"  "$pkg\resources\src\nodes\impl\chainner_native.dll" -Force
Copy-Item "$pkg\resources\src\packages\chaiNNer_standard\image\video_frames\save_video.py.$bak" `
          "$pkg\resources\src\packages\chaiNNer_standard\image\video_frames\save_video.py" -Force
```

---

## 11. Обязательный отчёт GitHub Copilot по завершении сборки

Copilot должен вернуть:
1. Ссылку на успешный Actions Run в репозитории `https://github.com/nnmgrp/chaiNNer-C/actions`.
2. Фактически зафиксированные версии компилятора (`clang-cl --version`), MSVC и SDK.
3. SHA-256 хэши и размеры скомпилированных `_chainner_graph.pyd` и `chainner_native.dll`.
4. Итог прогона `ruff check` и `ruff format --check`.
5. Результаты выполнения smoke-теста модуля графа.
6. Подтверждение отсутствия конфликтов с базой форка (v0.3.9 + TensorRT FFT + Smart Tiling).
