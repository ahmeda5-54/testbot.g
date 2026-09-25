@echo off
echo ===================================
echo     Uploading to GitHub
echo ===================================
echo.

git add .
git commit -m "Add Railway deployment guide and licensing system"
git push extra11 main

echo.
echo ===================================
echo     Upload Complete!
echo ===================================
pause
