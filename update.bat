@echo off
setlocal

REM Check if the version number argument is provided
if "%~1"=="" (
    echo Error: Version number argument is missing.
    exit /B 1
)

REM Save the version number argument to a variable
set "VERSION=%~1"

REM GitHub repository details
set "REPO_OWNER=nots0ggy"
set "REPO_NAME=cogm_logger"
set "ASSET_NAME=cogm-logger-installer.exe"


REM Temporary location to save the downloaded file
set "TEMP_DIR=%TEMP%\%RANDOM%"
mkdir "%TEMP_DIR%"
set "EXECUTABLE_PATH=%TEMP_DIR%\%ASSET_NAME%"

REM Download the release asset from GitHub. Releases are tagged with a
REM leading "v" (v1.9.0); the manifest passes the bare version (1.9.0), so
REM prefix it here to match the tag.
set "DOWNLOAD_URL=https://github.com/%REPO_OWNER%/%REPO_NAME%/releases/download/v%VERSION%/%ASSET_NAME%"
echo Downloading %ASSET_NAME% from %DOWNLOAD_URL%...
REM --fail makes curl exit non-zero on a 404 instead of writing the GitHub
REM error page to the file and exiting 0. Without it, a manifest bump that
REM lands before the release asset is published would leave us trying to run
REM an HTML page as the installer. With it, a missing asset just cleans up and
REM the existing install keeps working until the asset publishes.
curl --fail -L -o "%EXECUTABLE_PATH%" "%DOWNLOAD_URL%" || (
    echo Failed to download %ASSET_NAME%
    goto :cleanup
)

REM Execute the downloaded executable
echo Executing %ASSET_NAME%...
"%EXECUTABLE_PATH%"

:cleanup
REM Clean up temporary directory
if exist "%TEMP_DIR%" (
    rmdir /s /q "%TEMP_DIR%"
)

echo Script execution completed.
