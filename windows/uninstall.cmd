@echo off
rem Double-click to run uninstall.ps1 as administrator
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File ""%~dp0uninstall.ps1""'"
