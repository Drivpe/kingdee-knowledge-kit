# install.ps1 — kingdee-knowledge-kit 一键安装(Windows / 兜底路径)
#
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1
#   开关: -InstallRoot <dir>  -NoPath  -NoSkills  -NoVerify  -DryRun
#         -Harness workbuddy,zcode,opencode,pi,agents
#
# 主推安装方式(有 Python 环境时优先用它,升级卸载交给 pipx 管):
#   pipx install kingdee-knowledge-kit
#   然后: kd health
# 本脚本是兜底:无 Python 3 检测到 py/python 才用它,或要连技能一起装、
# 离线、双击运行(在资源管理器里右键 install.ps1 → 使用 PowerShell 运行)。
#
# 效果: kd CLI 装到 ~\.kingdee-kit,bin 加入用户 PATH,技能装到 ~\.agents\skills
#       (workbuddy/zcode/opencode/pi 用 junction 挂同一份),最后跑装机自检。
#
# 去服务化(工单 #20/#22):本脚本不再拷 service\、不再拉服务、不再验 /health
#       ——本地 HTTP 服务与十个端点已删除,kd 进程内直连 kd.core。
param(
    [string]$InstallRoot = (Join-Path $env:USERPROFILE ".kingdee-kit"),
    [switch]$NoPath,
    [switch]$NoSkills,
    [switch]$NoVerify,
    [switch]$DryRun,
    [string]$Harness = "all"
)
$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot      # 占位;真正的取值在下面「仓库根发现」里做,并且会校验布局
$Bin = Join-Path $InstallRoot "bin"

function Step($msg) { Write-Host "[install] $msg" }

# 仓库根发现(工单 I1,与 install.sh 对称):本脚本自身不含源码 —— 库体在 src\kd、
# 技能在 skills\、验证闸门在 tests\kd_regression.py,三处都从**检出目录**取。
# 原先这里只有一行 `$Repo = $PSScriptRoot`。而 `irm … | iex` 形态下 $PSScriptRoot
# 是**空的**(代码直接从字符串执行,没有脚本文件 —— PS 5.1 实测 `$PSScriptRoot=[]`),
# 于是 $Repo 为空,后面 Join-Path $Repo "src\kd" 拼出的相对路径按**当前目录**解析,
# 失败时报的是 Copy-Item 的"找不到路径"。而这个形态正是 README 列为一等公民的形式,
# 用户拿不到任何"该怎么装"的信息。故改为三级探测(任一命中都比"半截失败"好):
#   ① $PSScriptRoot(在检出目录里原地运行 —— 稳态用法)
#   ② 当前目录(iex 形态下若恰好在检出目录内,同样成立)
#   ③ 浅克隆到临时目录(iex 形态的兜底;需要 git + 能访问 GitHub)
# 三级都落空 ⇒ 打印可行动的用法后以非 0 退出,并清掉临时目录。
$RepoUrl = "https://github.com/Drivpe/kingdee-knowledge-kit.git"
$TempRepo = $null

# 期望布局 = src\kd 与 src\kd\contract.json 都在。缺 contract.json 的树是旧版
# (实测:远程 HEAD 曾在 src\kd 下放 v6.6 已删除的 query_routes.json),收下它只会把
# 失败推迟到「缺 lib\kd\contract.json」,看起来像仓库坏了 —— 那就不是"可行动的报错"了。
function Test-RepoLayout($p) {
    if (-not $p) { return $false }
    if (-not (Test-Path (Join-Path $p "src\kd"))) { return $false }
    return (Test-Path (Join-Path $p "src\kd\contract.json"))
}

# 失败一律走这里:临时克隆必须被清掉(装机失败还留一个临时目录是二次事故)。
function Fail($msg) {
    Write-Host "[install] ✗ $msg" -ForegroundColor Red
    if ($TempRepo -and (Test-Path $TempRepo)) {
        Remove-Item $TempRepo -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}

# 兜底:$ErrorActionPreference=Stop 下的终止性错误也要先清临时目录再退出。
trap {
    if ($TempRepo -and (Test-Path $TempRepo)) {
        Remove-Item $TempRepo -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[install] ✗ 未预期的错误:$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if (Test-RepoLayout $PSScriptRoot) { $Repo = $PSScriptRoot }
if (-not (Test-RepoLayout $Repo) -and (Test-RepoLayout (Get-Location).Path)) {
    $Repo = (Get-Location).Path
}
if (-not (Test-RepoLayout $Repo) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Step "未在检出目录内运行,先把仓库浅克隆到临时目录(装完即删)"
    $Repo = $null
    $gitExe = (Get-Command git).Source
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("kd-repo-" + [Guid]::NewGuid().ToString("N"))
    # 克隆必须**有界且不交互**:
    #   * GIT_TERMINAL_PROMPT=0 —— 仓库不可达/私有/需要凭据时 git 会弹凭据提示,
    #     在"一键脚本"里那就是一个永远等不到输入的挂起;关掉提示,失败得干脆。
    #   * 90 秒封顶 —— 实测到 GitHub 的连通性会间歇性挂住(同一命令有时秒回、
    #     有时几十秒无响应),而一个不会动的终端比报错更糟。超时即杀进程。
    #   * 判"克隆成功"只看**落地的东西能不能用**(Test-RepoLayout),不读进程退出码:
    #     PS 5.1 实测 Start-Process -PassThru 拿到的对象在进程退出后 **ExitCode 恒为空**
    #     (`exited=True exit=`,clone 明明成功),拿它判成功会让这条兜底在 Windows 上
    #     恒不成立 —— 一个只会走失败路径的兜底等于没有。
    $env:GIT_TERMINAL_PROMPT = "0"
    try {
        $proc = Start-Process -FilePath $gitExe `
            -ArgumentList @("clone", "--depth", "1", "--quiet", $RepoUrl, $tmp) `
            -NoNewWindow -PassThru
        if (-not $proc.WaitForExit(90000)) { $proc.Kill() }
    } catch {
        # 起不来与超时同路:都按"没克隆到"处理,由下面的布局判据收口。
    }
    if (Test-RepoLayout $tmp) {
        $Repo = $tmp; $TempRepo = $tmp
    } else {
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
        $Repo = $null
    }
}
if (-not (Test-RepoLayout $Repo)) {
    Write-Host @"
[install] ✗ 找不到仓库检出目录:既不是从检出目录内运行,也没能克隆到仓库。

  本脚本自身不含源码 —— 库体在 <检出目录>\src\kd、技能在 <检出目录>\skills、
  「离线组全绿才放行」的验证闸门在 <检出目录>\tests\kd_regression.py。
  所以必须有一个可用的检出目录,iex 形态也必须能把它取回来。

  正确用法(任选其一):
    1) 先克隆再原地运行(推荐;离线机器也适用):
         git clone --depth 1 https://github.com/Drivpe/kingdee-knowledge-kit.git
         cd kingdee-knowledge-kit
         powershell -ExecutionPolicy Bypass -File install.ps1
    2) 在已有的检出目录内运行(路径按你本地实际位置替换):
         powershell -ExecutionPolicy Bypass -File D:\path\to\kingdee-knowledge-kit\install.ps1
    3) iex 形态(需要 git 且能访问 GitHub —— 脚本会自己浅克隆到临时目录):
         irm https://raw.githubusercontent.com/Drivpe/kingdee-knowledge-kit/main/install.ps1 | iex
       ⚠️ iex 下发的是**远程那一版**脚本:远程未同步时拿到的是旧脚本,本脚本的探测
          逻辑根本不参与 —— 那种情况请走 1)。
"@ -ForegroundColor Red
    Fail "没有检出目录,装机中止"
}
Step "检出目录: $Repo"

# 1. Python 探测(仅用于兜底启动器;pipx 路径不需要本脚本)
$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
else { Fail "需要 Python 3.8+(未检测到 py/python)" }
Step "python: $pyCmd"

# 2. 库体 + 启动器
Step "安装到 $InstallRoot"
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "lib"), $Bin | Out-Null
    $libDst = Join-Path $InstallRoot "lib\kd"
    if (Test-Path $libDst) { Remove-Item $libDst -Recurse -Force }
    Copy-Item (Join-Path $Repo "src\kd") (Join-Path $InstallRoot "lib\") -Recurse -Force
    # 包内数据文件:对外契约声明(contract.json)。**v6.6 起它是唯一一个数据文件**
    # ——原 query_routes.json(拆解规则/预算/限速档)随拆词器与预算机制一并删除,
    # 其仍有效的两个值(路数上限、限速档)并入 contract.json 的 limits 段(ADR-0016)。
    # 缺它则内核静默退回内置兜底键集与默认值——装出来的行为与开发中的不是同一个东西。
    foreach ($f in @("contract.json")) {
        if (-not (Test-Path (Join-Path $libDst $f))) {
            Fail "缺 lib\kd\$f"
        }
    }

    $launcher = @'
#!/usr/bin/env python3
"""kd CLI launcher (generated by install.ps1; library body in ../lib/kd)."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "lib"))

from kd.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
'@
    $launcher | Set-Content -Path (Join-Path $Bin "kd.py") -Encoding UTF8

    # kd.cmd:Windows 不认 Unix shebang,裸名 kd 必须由 .cmd 承载。
    # ⚠️ 本文件必须 ASCII-only:cmd.exe 按 OEM 码页(zh-CN 上是 GBK)读 .cmd,
    # 非 ASCII 注释会吃掉后续命令。
    $cmd = @'
@echo off
rem kd.cmd - kingdee-knowledge CLI wrapper for Windows cmd / PowerShell.
rem
rem Windows does not honour Unix shebangs: it resolves executables by extension
rem (.exe/.cmd/.bat), so the extension-less bin/kd (bash shim) is unreachable
rem from cmd/PowerShell. Conversely MSYS2/Git Bash does not resolve the bare
rem name `kd` to kd.cmd. Both shims must therefore exist.
rem
rem Python discovery: `py` launcher first (picks a 3.x automatically), then
rem `python` on PATH.
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
'@
    # 写 .cmd 必须显式用 ASCII:PowerShell 5.1 的 -Encoding UTF8 会加 BOM,
    # 且若内容含非 ASCII 会被写坏。改用 .NET 的 ASCII 编码,零 BOM。
    [System.IO.File]::WriteAllText((Join-Path $Bin "kd.cmd"), $cmd,
        (New-Object System.Text.ASCIIEncoding))

    # bin/kd:无扩展名 bash shim,给 Git Bash(MSYS2)用——它不会把裸名 kd 解析到 .cmd。
    $sh = @'
#!/usr/bin/env bash
# cd to the script directory then call python via a relative path: this avoids
# MSYS/GitBash rewriting POSIX absolute paths when passing them to native
# Windows python. Linux/macOS/WSL behaviour is unchanged.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" && exec python3 ./kd.py "$@"
'@
    # 无扩展名的 shim 用 UTF8(无 BOM)写:bash 不认 BOM。
    [System.IO.File]::WriteAllText((Join-Path $Bin "kd"), $sh,
        (New-Object System.Text.UTF8Encoding $false))
}

# 3. PATH(bin 加入用户环境变量,幂等)
if (-not $NoPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -and $userPath.Split(";") -contains $Bin) {
        Step "PATH 已包含 $Bin(跳过)"
    } else {
        Step "加入用户 PATH: $Bin(新开终端生效)"
        if (-not $DryRun) {
            $newPath = if ($userPath) { "$userPath;$Bin" } else { $Bin }
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        }
    }
}

# 4. 技能:实体只装一份,放在通用兼容目录 ~\.agents\skills\kingdee-knowledge
#    (agentskills.io 标准,Codex/Claude Code/opencode 原生读取);WorkBuddy/ZCode/pi
#    的目录用 NTFS junction 挂到同一份(junction 无需管理员权限,也不跨卷失败)
#    ——升级一处、全家生效;junction 失败回退为真实拷贝。
if (-not $NoSkills) {
    $skillSrc = Join-Path $Repo "skills\kingdee-knowledge\skills\kingdee-knowledge"
    $canon = Join-Path $env:USERPROFILE ".agents\skills\kingdee-knowledge"
    Step "技能实体 → $canon"
    if (-not $DryRun) {
        if ((Test-Path (Join-Path $canon "SKILL.md")) -and
            -not ((Get-Item $canon -ErrorAction SilentlyContinue).LinkType)) {
            Copy-Item (Join-Path $canon "SKILL.md") (Join-Path $canon "SKILL.md.bak") -Force
        }
        if (Test-Path $canon) { Remove-Item $canon -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $canon | Out-Null
        Copy-Item (Join-Path $skillSrc "*") $canon -Recurse -Force

        $harnessMap = [ordered]@{
            "workbuddy" = Join-Path $env:USERPROFILE ".workbuddy\skills\kingdee-knowledge"
            "zcode"     = Join-Path $env:USERPROFILE ".zcode\skills\kingdee-knowledge"
            "opencode"  = Join-Path $env:USERPROFILE ".config\opencode\skills\kingdee-knowledge"
            "pi"        = Join-Path $env:USERPROFILE ".pi\agent\skills\kingdee-knowledge"
        }
        $targets = if ($Harness -eq "all") { $harnessMap.Keys } else {
            $Harness.Split(",") | ForEach-Object { $_.Trim().ToLower() }
        }
        foreach ($h in $targets) {
            if (-not $harnessMap.Contains($h)) {
                Write-Host "[install] 未知 harness: $h(可选 workbuddy/zcode/opencode/pi/agents/all)" -ForegroundColor Yellow
                continue
            }
            $dest = $harnessMap[$h]
            $parent = Split-Path $dest -Parent
            if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
            try {
                New-Item -ItemType Junction -Path $dest -Target $canon | Out-Null
                Step "技能 → $dest(junction → $canon)"
            } catch {
                New-Item -ItemType Directory -Force -Path $dest | Out-Null
                Copy-Item (Join-Path $skillSrc "*") $dest -Recurse -Force
                Step "技能 → $dest(拷贝;junction 创建失败已回退)"
            }
        }
    }
    Write-Host "  (ZCode 也可用插件面板: Settings→Plugins→Discover→添加本仓库 GitHub 地址→Get)"
}

# 5. 装机自检:验「kd 可执行且真能跑通一条命令」。
#    判据是 tests\kd_regression.py 的离线组(工单 #21;旧的 verify_ksearch.py 已随
#    去服务化删除)——它既不联网也不需要服务,全绿才放行。历史上安装器漏拷过
#    query_routes.json 与 kd.py,装出「残废版」却毫无报错,这里就是那道闸。
#    先补验装出来的那个 kd 本身能跑(回归脚本走仓库内核,不覆盖 bin\kd.cmd 装坏)。
#    ⚠️ 公开面守卫 scripts\check_core_surface.py 已删除(决策 D2,2026-09-27):
#    真正防静默失效的三条判据(版本单一真源 / health 依赖可解析 / kind 集合一致)
#    已迁进回归离线组,故这里只需跑回归。
if (-not $DryRun -and -not $NoVerify) {
    Step "冒烟验证:$Bin\kd.cmd health"
    if (Test-Path (Join-Path $Bin "kd.cmd")) {
        & (Join-Path $Bin "kd.cmd") health | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Fail "kd 装出来后无法执行,装机失败"
        }
    } else {
        # 没有 .cmd(非 Windows 或 -NoPath 场景)就直接用 python 调启动器
        & $pyCmd (Join-Path $Bin "kd.py") health | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Fail "kd 装出来后无法执行,装机失败"
        }
    }
    # 闸门路径取自上面探测到的 $Repo(不是 $PSScriptRoot、也不是当前目录),故原地运行与
    # iex+浅克隆两种形态下都指向同一个检出目录。仍然显式校验存在性:缺 tests\ 时给一句
    # 人话,而不是让 python 抛 FileNotFoundError —— 闸门读不到就是"没放行",不可能是通过。
    $regression = Join-Path $Repo "tests\kd_regression.py"
    if (-not (Test-Path $regression)) {
        Fail "检出目录缺 $regression,无法执行「离线组全绿才放行」那道闸门"
    }
    Step "装机自检:$regression(离线组,不联网)"
    & $pyCmd $regression
    if ($LASTEXITCODE -ne 0) {
        Fail "回归未全绿,检查上方 FAIL 项"
    }
    Step "✓ kd 可执行且回归通过"
} elseif ($NoVerify) {
    Step "跳过装机自检(-NoVerify)"
}

Write-Host ""
Write-Host "主推安装方式(有 Python 环境时优先用它,升级卸载交给 pipx 管):" -ForegroundColor Green
Write-Host "  pipx install kingdee-knowledge-kit"
Write-Host "  kd health"
Write-Host ""
Write-Host "完成!试一试:" -ForegroundColor Green
Write-Host "  kd health                                # 内核自检(库模式:无服务、无端口)"
Write-Host "  kd search --kw ""信用额度控制"" --product 93   # 出清单(标题级,带 hitRoutes/routes),你自己挑"
Write-Host "  kd search --kw ""信用额度"" --kw ""应收单 信用""   # 拆好词按序传入:每个 --kw 一路,内核不再自行拆词"
Write-Host "  kd read <id> --kind question             # 取全文,kind 照抄 search 结果的 type"
Write-Host ""
Write-Host "本套件不合成回答(ADR-0008,零模型依赖):kd search 只出清单,挑中的条目用 kd read 取全文,"
Write-Host "再由你按 docs/ANSWER-SPEC.md 自己合成。排序由上游综合排序决定,内核只去重、零评分。"
Write-Host ""
Write-Host "技能实体在 ~\.agents\skills\kingdee-knowledge(通用兼容,Codex/Claude Code/opencode 直接读取)," -ForegroundColor Green
Write-Host "WorkBuddy/ZCode/pi 目录已用 junction 挂到同一份——升级重跑本脚本一次即全家生效。" -ForegroundColor Green
Write-Host "只想装部分 harness: -Harness zcode,pi"

# 临时克隆用完即删:走到这里说明库体、技能(可选)与验证都已从它取完,再留着就是垃圾。
# (中途失败不在这里 —— 那些路径统一走 Fail,由它清理。)
if ($TempRepo -and (Test-Path $TempRepo)) {
    Remove-Item $TempRepo -Recurse -Force -ErrorAction SilentlyContinue
}
