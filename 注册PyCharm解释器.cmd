@echo off
chcp 65001 >nul
title 注册 PyCharm 解释器
echo 本脚本会把本工程的解释器写进 PyCharm 的 SDK 表。
echo 执行前请**先关闭 PyCharm**，否则写入会被 IDE 退出时覆盖。
echo.
pause
"C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" "%~dp0tools\注册PyCharm解释器.py"
echo.
pause
