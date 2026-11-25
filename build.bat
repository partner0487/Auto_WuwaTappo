@echo off
rem 打包成單一 exe：dist\AutoWuwaTappo.exe
rem --uac-admin：開啟時要求系統管理員權限（WuwaTappo 是管理員程式，否則點擊會被擋）
cd /d "%~dp0"
rem 有 logo 原圖才重新產生圖示，否則直接用 repo 裡的 assets\icon.ico / icon.png
if exist assets\logo.jfif (python tools\make_icon.py || exit /b 1)
pyinstaller --noconfirm --clean --onefile --windowed --uac-admin ^
  --name AutoWuwaTappo ^
  --icon assets\icon.ico ^
  --add-data "templates;templates" ^
  --add-data "assets\icon.png;assets" ^
  --collect-all winrt ^
  --hidden-import winocr ^
  %* tray_app.py
