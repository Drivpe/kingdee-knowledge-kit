@echo off
rem kd.cmd — 金蝶官方知识 CLI 包装(Windows cmd / PowerShell)。
rem Windows 不认 Unix 她bang,只认扩展名,故必须与无扩展名的 cli/kd(bash shim)并存:
rem Git Bash 不会把裸名 kd 解析到 kd.cmd,反之 cmd 也不认无扩展名的 kd。
rem Python 解释器探测:py 启动器优先(能自动选 3.x),再退 PATH 上的 python / python3。
rem 本文件曾把路径写死为 %LOCALAPPDATA%\...\Python312\python.exe,装了 3.11/3.13
rem 或非默认安装位置即静默失效,报错还只是"不是内部或外部命令"——故改为探测。
setlocal
set "KDPY="
for %%P in (py python python3) do (
    if not defined KDPY (
        %%P -c "import sys" >nul 2>&1 && set "KDPY=%%P"
    )
)
if not defined KDPY (
    echo kd: 未找到可用的 Python^(需要 3.8+^);请安装 Python 或重跑安装器 1>&2
    exit /b 1
)
%KDPY% "%~dp0kd.py" %*
