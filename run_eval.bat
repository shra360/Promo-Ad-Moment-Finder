@echo off
rem Promo Finder: full evaluation on Windows.
rem   run_eval.bat          all 50 episodes  -> runs\ai
rem   run_eval.bat test     episodes 1-3     -> runs\ai_test   (try this first)
setlocal
cd /d "%~dp0"
set TITLE=I Married a Broken Soldier. He was a Duke

where python >nul 2>nul || (echo Python not found. Open "Anaconda Prompt", cd to this folder, and run run_eval.bat again. & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || (echo Python 3.10 or newer is needed. & exit /b 1)

if not exist .env if "%GEMINI_API_KEY%"=="" (
  set /p GEMINI_API_KEY=Paste your Gemini API key and press Enter: 
)

echo Installing requirements...
python -m pip install -q -r requirements.txt || exit /b 1

echo Checking the model and key...
python -m promo_finder ping || exit /b 1

echo No-AI baseline...
python -m promo_finder run --input data\raw --out runs\baseline --mode baseline --title "%TITLE%" --reference eval\reference.json || exit /b 1

if /i "%~1"=="test" (
  python -m promo_finder run --input data\raw --out runs\ai_test --episodes 1-3 --title "%TITLE%" --reference eval\reference.json
) else (
  python -m promo_finder run --input data\raw --out runs\ai --title "%TITLE%" --baseline runs\baseline --reference eval\reference.json
)
if errorlevel 1 (
  echo.
  echo The run stopped early. Finished model calls are cached in .cache - run the same command again to continue.
  exit /b 1
)
echo.
echo Done. Open runs\ai\report.md and runs\ai\eval_report.md, or run:  streamlit run app.py
