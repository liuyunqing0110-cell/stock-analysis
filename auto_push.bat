@echo off
chcp 65001 >nul
git add .
git commit -m "update: %date% %time%"
git push origin main
pause