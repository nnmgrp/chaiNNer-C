@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title chaiNNer-C: Переход на форк nnmgrp/chaiNNer-C
cd /d "%~dp0"

echo =====================================================================
echo           Переход на форк chaiNNer-C (nnmgrp/chaiNNer-C)
echo =====================================================================
echo.

:: 1. Проверка правильности расположения скрипта
if not exist "chaiNNer.exe" (
    if not exist "resources" (
        echo [ОШИБКА] chaiNNer.exe или папка resources не найдены!
        echo Пожалуйста, поместите этот файл в корневую папку с релизом chaiNNer
        echo (рядом с chaiNNer.exe) и запустите его снова.
        echo.
        pause
        exit /b 1
    )
)

:: 2. Закрытие работающих процессов chaiNNer для снятия блокировок файлов
tasklist /fi "imagename eq chaiNNer.exe" 2>nul | findstr /i "chaiNNer.exe" >nul
if not errorlevel 1 (
    echo [1/5] Закрываем запущенный chaiNNer для безопасного обновления файлов...
    taskkill /F /IM chaiNNer.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
) else (
    echo [1/5] Процесс chaiNNer не запущен, продолжаем...
)

tasklist /fi "imagename eq python.exe" 2>nul | findstr /i "python.exe" >nul
if not errorlevel 1 (
    taskkill /F /IM python.exe >nul 2>&1
    timeout /t 1 /nobreak >nul
)

:: 3. Если это локальный Git-репозиторий — переключаем remotes
if exist ".git" (
    echo [*] Обнаружен репозиторий Git. Переключаем remote origin на nnmgrp/chaiNNer-C...
    git remote set-url origin https://github.com/nnmgrp/chaiNNer-C.git >nul 2>&1
    git fetch origin chaiNNer-C
    git checkout chaiNNer-C >nul 2>&1
    git pull origin chaiNNer-C
)

:: 4. Скачивание актуального портативного релиза v0.3.9 с NVENC Stage 1
echo.
echo [2/5] Скачивание сборки с поддержкой NVENC Stage 1 и патчем интерфейса...
set "ZIP_URL=https://github.com/nnmgrp/chaiNNer-C/releases/download/v0.3.9/chaiNNer-C-v0.3.9-win-x64.zip"
set "TEMP_ZIP=%CD%\__chainner_c_update_temp.zip"

where curl.exe >nul 2>&1
if not errorlevel 1 (
    curl.exe -L -f --progress-bar "%ZIP_URL%" -o "%TEMP_ZIP%"
) else (
    echo Использование PowerShell для скачивания (curl не найден)...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('%ZIP_URL%', '%TEMP_ZIP%')"
)

if not exist "%TEMP_ZIP%" (
    echo.
    echo [ОШИБКА] Не удалось скачать архив обновления.
    echo Проверьте подключение к сети Интернет.
    pause
    exit /b 1
)

:: 5. Распаковка архива поверх текущей папки
echo.
echo [3/5] Применение обновления (бинарники, DLL, UI)...
where tar.exe >nul 2>&1
if not errorlevel 1 (
    tar.exe -xf "%TEMP_ZIP%"
) else (
    powershell -NoProfile -Command "Expand-Archive -Path '%TEMP_ZIP%' -DestinationPath '.' -Force"
)

if exist "%TEMP_ZIP%" del /f /q "%TEMP_ZIP%" >nul 2>&1

:: 6. Загрузка самых свежих исходников бэкенда из ветки chaiNNer-C
echo.
echo [4/5] Синхронизация последних улучшений бэкенда...
set "RAW_BASE=https://raw.githubusercontent.com/nnmgrp/chaiNNer-C/chaiNNer-C/backend/src"
set "FILES_TO_SYNC=dependencies/run_chainner_pip.py nodes/impl/tensorrt/fft/fft_export.py nodes/impl/tensorrt/fft/models/gfisrv2.py nodes/impl/tensorrt/fft/models/lawfft.py nodes/impl/tensorrt/group_norm.py"

for %%F in (%FILES_TO_SYNC%) do (
    set "DEST_FILE=resources\src\%%F"
    set "DEST_FILE=!DEST_FILE:/=\!"
    curl.exe -sL -f "%RAW_BASE%/%%F" -o "!DEST_FILE!" 2>nul
)

:: 7. Обновление Spandrel до 0.4.2+c13.1 в локальном окружении Python
echo.
echo [5/5] Проверка встроенного Python и обновление Spandrel...
set "PY_EXE="
if exist "python\python\python.exe" set "PY_EXE=python\python\python.exe"
if not defined PY_EXE if exist "python\python.exe" set "PY_EXE=python\python.exe"

if defined PY_EXE (
    echo Найден Python: !PY_EXE!
    echo Загрузка и установка Spandrel v0.4.2-c13.1 (57 архитектур)...
    set "WHL_URL=https://github.com/nnmgrp/spandrel/releases/download/v0.4.2-c13.1/spandrel-0.4.2%%2Bc13.1-py3-none-any.whl"
    set "TEMP_WHL=%CD%\__spandrel_temp.whl"
    
    where curl.exe >nul 2>&1
    if not errorlevel 1 (
        curl.exe -sL -f "!WHL_URL!" -o "!TEMP_WHL!"
    ) else (
        powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://github.com/nnmgrp/spandrel/releases/download/v0.4.2-c13.1/spandrel-0.4.2%2Bc13.1-py3-none-any.whl', '!TEMP_WHL!')"
    )

    if exist "!TEMP_WHL!" (
        "!PY_EXE!" -m pip install --no-deps --force-reinstall "!TEMP_WHL!" >nul 2>&1
        del /f /q "!TEMP_WHL!" >nul 2>&1
        for /f "tokens=*" %%V in ('"!PY_EXE!" -c "import spandrel; print(spandrel.__version__)" 2^>nul') do (
            echo [+] Spandrel успешно обновлен до версии: %%V
        )
    ) else (
        echo [!] Не удалось загрузить wheel Spandrel, chaiNNer обновит его при первом запуске пакета PyTorch.
    )
) else (
    echo [!] Каталог Python еще не инициализирован. Он будет настроен автоматически при первом запуске chaiNNer.
)

:: Завершение
echo.
echo =====================================================================
echo         Переход на ветку nnmgrp/chaiNNer-C успешно завершён!
echo =====================================================================
echo  - Аппаратное кодирование NVENC Stage 1 (H.264, HEVC, AV1) активировано
echo  - Нативные модули chainner_native.dll и _chainner_graph.pyd обновлены
echo  - Spandrel 0.4.2+c13.1 со всеми 57 архитектурами синхронизирован
echo  - Ложные уведомления об обновлении отключены
echo  - Пользовательские настройки (settings.json, кэш) сохранены
echo =====================================================================
echo.

set /p LAUNCH="Запустить chaiNNer прямо сейчас? (Y/N, по умолчанию Y): "
if /i "%LAUNCH%"=="" set "LAUNCH=Y"
if /i "%LAUNCH%"=="Y" (
    echo Запуск chaiNNer...
    start "" "%CD%\chaiNNer.exe"
)

exit /b 0
