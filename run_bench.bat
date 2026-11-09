@echo off
rem ReelBench: run the AI pipeline on the synthetic 5-show pack and score it against its ground truth.
rem   run_bench.bat        first 8 episodes of each show, 40 in all (about 370 model calls)
rem   run_bench.bat all    all 160 episodes (about 1,450 model calls; on the free tier this can take days)
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Open "Anaconda Prompt", cd to this folder, and run run_bench.bat again. & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || (echo Python 3.10 or newer is needed. & exit /b 1)
if not exist .env if "%GEMINI_API_KEY%"=="" (
  set /p GEMINI_API_KEY=Paste your Gemini API key and press Enter: 
)
python -m pip install -q -r requirements.txt || exit /b 1
python -m promo_finder ping || exit /b 1

if /i "%~1"=="all" (set SUB=& set TAG=all) else (set SUB=--per-series 8& set TAG=sub8)
echo No-AI baseline on the same episodes...
python eval\reelbench.py run --bench data\reelbench --out bench\baseline_%TAG% --mode baseline %SUB% || exit /b 1
python eval\reelbench.py score --bench data\reelbench --runs bench\baseline_%TAG% > nul
echo AI pipeline...
python eval\reelbench.py run --bench data\reelbench --out bench\ai_%TAG% %SUB%
if errorlevel 1 (
  echo.
  echo The run stopped early. Finished model calls are cached in .cache - run the same command again to continue.
  exit /b 1
)
python eval\reelbench.py score --bench data\reelbench --runs bench\ai_%TAG%
echo.
echo Scores: bench\ai_%TAG%\reelbench_report.md   (baseline: bench\baseline_%TAG%\reelbench_report.md)
