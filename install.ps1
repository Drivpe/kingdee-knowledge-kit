# install.ps1 — kingdee-knowledge-kit 一键安装(Windows)
# 用法: powershell -ExecutionPolicy Bypass -File install.ps1
#   开关: -InstallRoot <dir>  -NoPath  -NoSkills  -NoStart  -DryRun  -Port 4097
# 效果: 服务+kd CLI 装到 ~\.kingdee-kit,bin 加入用户 PATH,技能装到 ~\.agents\skills,
#       启动服务并自动跑回归验证
param(
    [string]$InstallRoot = (Join-Path $env:USERPROFILE ".kingdee-kit"),
    [switch]$NoPath,
    [switch]$NoSkills,
    [switch]$NoStart,
    [switch]$DryRun,
    [int]$Port = 4097
)
$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$Bin = Join-Path $InstallRoot "bin"

function Step($msg) { Write-Host "[install] $msg" }

# 1. Python 探测
$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
else { Write-Host "[install] 需要 Python 3.8+(未检测到 py/python)" -ForegroundColor Red; exit 1 }
Step "python: $pyCmd"

# 2. 复制 service + cli
Step "安装到 $InstallRoot"
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallRoot "service"), $Bin, (Join-Path $InstallRoot "logs") | Out-Null
    Copy-Item (Join-Path $Repo "service\kingdee-ksearch-service.py") (Join-Path $InstallRoot "service\") -Force
    Copy-Item (Join-Path $Repo "service\docstore.py") (Join-Path $InstallRoot "service\") -Force
    Copy-Item (Join-Path $Repo "service\semantic_rerank.py") (Join-Path $InstallRoot "service\") -Force
    # 多路检索规则文件(ADR-0005/0009):缺它则原句路/症状词路/实体规则/产品别名全部失效,
    # 服务会静默退回内置默认值——装出来的行为与开发中的不是同一个东西。
    Copy-Item (Join-Path $Repo "service\query_routes.json") (Join-Path $InstallRoot "service\") -Force
    Copy-Item (Join-Path $Repo "cli\kd.py") $Bin -Force
    Copy-Item (Join-Path $Repo "cli\kd.cmd") $Bin -Force
    # 同时装无扩展名的 bash shim:Windows 上的 Git Bash(MSYS2)不会把裸名 kd 解析到
    # .cmd,只有 kd.cmd 的话 Git Bash 里敲 kd 找不到。
    Copy-Item (Join-Path $Repo "cli\kd") $Bin -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $Repo "tests") | Out-Null
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

# 4. 技能(~\.agents\skills 是 ZCode/Claude Code/Codex/Cursor 通用标准目录)
if (-not $NoSkills) {
    $skillDst = Join-Path $env:USERPROFILE ".agents\skills\kingdee-knowledge"
    $skillSrc = Join-Path $Repo "skills\kingdee-knowledge\skills\kingdee-knowledge"
    Step "技能 → $skillDst"
    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $skillDst | Out-Null
        if (Test-Path (Join-Path $skillDst "SKILL.md")) {
            Copy-Item (Join-Path $skillDst "SKILL.md") (Join-Path $skillDst "SKILL.md.bak") -Force
        }
        Copy-Item (Join-Path $skillSrc "SKILL.md") $skillDst -Force
    }
    Write-Host "  (ZCode 也可用插件面板: Settings→Plugins→Discover→添加本仓库 GitHub 地址→Get)"
}

# 5. 启动服务
if (-not $NoStart) {
    Step "启动服务(:$Port)"
    if (-not $DryRun) {
        & (Join-Path $Repo "scripts\start-service.ps1") -Port $Port -InstallRoot $InstallRoot
        if ($LASTEXITCODE -ne 0) { Write-Host "[install] 服务启动失败" -ForegroundColor Red; exit 1 }
    }
} else {
    Step "跳过启动(-NoStart)"
}

# 5.5 装机自检(--NoStart 时也必须做)
# 历史上安装器漏拷过 semantic_rerank.py 与 query_routes.json——服务要么起不来,
# 要么静默退回默认值装出"残废版"却毫无报错。这里只验文件齐 + 配置可解析,不跑完整回归。
if (-not $DryRun) {
    Step "装机自检"
    $need = @("service\kingdee-ksearch-service.py", "service\docstore.py", "service\semantic_rerank.py",
              "service\query_routes.json", "bin\kd.py", "bin\kd.cmd")
    $missing = @()
    foreach ($f in $need) {
        if (-not (Test-Path (Join-Path $InstallRoot $f))) { $missing += $f }
    }
    if ($missing.Count -gt 0) {
        Write-Host ("[install] 装机自检失败:缺文件 " + ($missing -join ", ")) -ForegroundColor Red
        exit 1
    }
    $cfgPath = Join-Path $InstallRoot "service\query_routes.json"
    try {
        $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $cfg.budget) { throw "query_routes.json 不含 budget 配置" }
    } catch {
        Write-Host "[install] 装机自检失败:$($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    Step "✓ 文件齐备(含 query_routes.json 规则文件)"
}

# 6. 回归验证
if (-not $NoStart -and -not $DryRun) {
    Step "回归验证(含 CLI、资料包与召回信号契约)"
    $env:KD_PY = Join-Path $Bin "kd.py"
    $env:KSEARCH_URL = "http://127.0.0.1:$Port"
    & $pyCmd (Join-Path $Repo "tests\verify_ksearch.py")
    if ($LASTEXITCODE -ne 0) { Write-Host "[install] 回归未全绿,检查上方 FAIL 项" -ForegroundColor Red; exit 1 }
}

Write-Host ""
Write-Host "完成!试一试:" -ForegroundColor Green
Write-Host "  kd search ""信用额度控制"" --product 93"
Write-Host "  kd read <id> --kind answer               # 读全文,kind 照抄 search 结果的 type"
Write-Host "  kd ask ""信用额度怎么控制"" --topk 4      # 资料包(带 synthesisBrief 召回信号),交给调用方 agent 合成"
Write-Host "  kd manifest                              # 全部能力清单"
Write-Host ""
Write-Host "本套件不合成回答(ADR-0008,零模型依赖):拿到 kd ask 资料包后,由 agent 按"
Write-Host "docs/ANSWER-SPEC.md 合成;子代理提示词模板见技能 SKILL.md。"
