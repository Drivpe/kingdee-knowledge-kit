@echo off
rem kd.cmd - kingdee-knowledge CLI wrapper for Windows cmd / PowerShell.
rem
rem Windows does not honour Unix shebangs: it resolves executables by extension
rem (.exe/.cmd/.bat), so the extension-less cli/kd (bash shim) is unreachable
rem from cmd/PowerShell. Conversely MSYS2/Git Bash does not resolve the bare
rem name `kd` to kd.cmd. Both shims must therefore exist.
rem
rem Python discovery: `py` launcher first (picks a 3.x automatically), then
rem `python` on PATH. This file used to hardcode
rem   %LOCALAPPDATA%\Programs\Python\Python312\python.exe
rem which silently broke for anyone on 3.11/3.13 or a non-default install root,
rem failing with only "not recognized as an internal or external command".
rem
rem NOTE: keep this file ASCII-only. cmd.exe reads .cmd in the OEM codepage
rem (GBK on zh-CN Windows); non-ASCII comments corrupt command parsing.
setlocal
set "KDPY="
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 set "KDPY=py"
if defined KDPY goto :run
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 set "KDPY=python"
:run
if not defined KDPY (
    echo kd: no usable Python found ^(3.8+ required^). Install Python or re-run the installer. 1>&2
    exit /b 1
)
%KDPY% "%~dp0kd.py" %*
