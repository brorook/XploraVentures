@echo off
echo ============================================
echo  XploraVentures - Build Executable
echo ============================================
echo.

echo [1/4] Pausing OneDrive sync (prevents build folder lock)...
taskkill /f /im OneDrive.exe >nul 2>&1

echo Clearing previous build...
if exist build rd /s /q build
if exist dist\XploraVentures.exe del /f /q dist\XploraVentures.exe

echo.
echo [2/4] Installing / updating dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is installed and on PATH.
    start OneDrive.exe
    pause
    exit /b 1
)

echo.
echo [3/4] Building executable...
pyinstaller XploraVentures.spec
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed. See output above.
    start OneDrive.exe
    pause
    exit /b 1
)

echo.
echo [4/4] Restarting OneDrive...
start OneDrive.exe

echo.
echo Done!
echo.
echo Executable located at:
echo   dist\XploraVentures.exe
echo.
echo ── Release checklist (automated via GitHub Actions) ─
echo  1. Bump VERSION in XploraVentures.py  (e.g. "1.1.0")
echo  2. Commit and push your changes
echo  3. git tag v1.1.0
echo  4. git push --tags
echo  GitHub Actions will build and publish the release automatically.
echo  Users will see the update banner on next dashboard launch.
echo ─────────────────────────────────────────────────────
echo.
pause
