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
    Copy-Item (Join-Path $Repo "cli\kd.py") $Bin -Force
    Copy-Item (Join-Path $Repo "cli\kd.cmd") $Bin -Force
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

# 6. 回归验证
if (-not $NoStart -and -not $DryRun) {
    Step "回归验证(22 项内含 CLI 与 kd ai 降级)"
    $env:KD_PY = Join-Path $Bin "kd.py"
    $env:KSEARCH_URL = "http://127.0.0.1:$Port"
    & $pyCmd (Join-Path $Repo "tests\verify_ksearch.py")
    if ($LASTEXITCODE -ne 0) { Write-Host "[install] 回归未全绿,检查上方 FAIL 项" -ForegroundColor Red; exit 1 }
}

Write-Host ""
Write-Host "完成!试一试:" -ForegroundColor Green
Write-Host "  kd search ""信用额度控制"" --product 93"
Write-Host "  kd read <id> --kind answer               # 读全文,kind 照抄 search 结果的 type"
Write-Host "  kd ask ""信用额度怎么控制"" --topk 4      # 资料包,交给你的 AI 合成"
Write-Host "  kd ai ""信用额度怎么控制""                # 一步合成带引用回答(需模型通道,自动降级)"
Write-Host "  kd manifest                              # 全部能力清单"
Write-Host ""
Write-Host "kd ai 模型通道(可选,任意 OpenAI 兼容端点):"
Write-Host '  $env:KAI_BASE  = "http://127.0.0.1:4090"   # 默认值,勿带 /v1'
Write-Host '  $env:KAI_MODEL = "glm-5.3-flash"            # 默认值'
