@echo off
rem Builds a standalone "YT Downloader.exe" in the dist folder.
cd /d "%~dp0"

echo Installing / updating dependencies...
python -m pip install -U -r requirements.txt pyinstaller || goto :error

echo Building...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "YT Downloader" ^
  --add-data "web;web" ^
  --collect-all yt_dlp ^
  --collect-all webview ^
  web_app.py || goto :error

rem Ship ffmpeg next to the exe so it works on PCs without ffmpeg installed.
for %%F in (ffmpeg.exe ffprobe.exe) do (
  for /f "delims=" %%P in ('where %%F 2^>nul') do (
    if not exist "dist\%%F" copy /y "%%P" "dist\%%F" >nul && echo Copied %%F
  )
)

echo.
echo Done! Your app is in the "dist" folder.
echo Keep ffmpeg.exe and ffprobe.exe next to "YT Downloader.exe" if you move it.
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
