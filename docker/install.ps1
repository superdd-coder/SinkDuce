# SinkDuce Windows one-line deploy — Docker Desktop + WSL2 bootstrap + compose.
#
# Run in PowerShell (Win10 2004+ / Win11):
#   irm https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/install.ps1 | iex
#
# Re-runnable bootstrap: each run detects current state and installs whatever
# is missing. WSL2 install requires admin + one REBOOT — after rebooting, run
# the same command again and it continues from where it stopped.
# Messages are bilingual (Chinese + English).

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
try {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
} catch {}

$Base = "https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev"
$Dir  = Join-Path $HOME "sinkduce"
$DockerDesktopExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

function Say([string]$msg)  { Write-Host "==> $msg" }
function Info([string]$msg) { Write-Host "    $msg" }
function Halt([string]$zh, [string]$en) {
  Write-Host ""
  Write-Host "✗ $zh" -ForegroundColor Red
  Write-Host "✗ $en" -ForegroundColor Red
  return
}

function Test-Command([string]$name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Test-DockerEngine {
  if (-not (Test-Command docker)) { return $false }
  & docker info 2>$null 1>$null
  return ($LASTEXITCODE -eq 0)
}

function Test-Wsl {
  # wsl.exe exists even on machines without WSL (system stub) — must run a
  # real command and check the exit code. Output may be UTF-16: strip nulls.
  if (-not (Test-Command wsl.exe)) { return $false }
  $null = (& wsl.exe --status 2>&1 | Out-String) -replace "\0", ""
  return ($LASTEXITCODE -eq 0)
}

function Test-IsAdmin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Run $cmd in an elevated PowerShell (UAC prompt). Returns $true on exit 0.
function Invoke-Elevated([string]$cmd) {
  try {
    $p = Start-Process powershell -Verb RunAs -Wait -PassThru `
           -ArgumentList "-NoProfile", "-Command", $cmd
    return ($p.ExitCode -eq 0)
  } catch {
    return $false  # user cancelled the UAC prompt
  }
}

Say "SinkDuce Windows 部署向导 / Windows deploy wizard"
Say "（缺什么装什么；WSL 装完需重启后重跑本命令 / installs missing pieces; reboot once after WSL, then re-run）"

# ── 步骤 1/4：WSL2（Docker Desktop 的前置） ─────────────────────────────
if (-not (Test-Wsl)) {
  $build = [Environment]::OSVersion.Version.Build
  if ($build -lt 19041) {
    Halt "Windows 版本过旧（需 Win10 2004+/Win11），请先更新系统再运行" `
         "Windows build too old (need 19041+), please run Windows Update first"
    return
  }
  Say "未检测到 WSL2，开始安装 / Installing WSL2 (UAC + reboot required)"
  $wslCmd = "wsl --install"
  if (Test-IsAdmin) {
    & wsl --install
  } elseif (-not (Invoke-Elevated $wslCmd)) {
    Halt "已取消管理员授权，未安装 WSL2 / admin prompt cancelled — WSL2 not installed"
    return
  }
  Write-Host ""
  Halt "WSL2 安装完成/进行中：请重启电脑，然后重新运行本条命令继续 / WSL2 done installing — REBOOT now, then re-run this same command to continue"
  return
}
Say "WSL2 已就绪 / WSL2 present"

# ── 步骤 2/4：Docker Desktop ────────────────────────────────────────────
if (-not (Test-Command docker)) {
  Say "未检测到 Docker，安装 Docker Desktop（UAC + 数分钟）/ Installing Docker Desktop"
  $installer = Join-Path $env:TEMP "DockerDesktopInstaller.exe"
  try {
    Invoke-WebRequest "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe" `
      -OutFile $installer -UseBasicParsing
  } catch {
    Halt "Docker Desktop 安装器下载失败，请检查网络 / failed to download the installer"
    return
  }
  $installCmd = "& '$installer' install --quiet --accept-license"
  if (-not (Invoke-Elevated $installCmd)) {
    Halt "Docker Desktop 安装未完成 / Docker Desktop install did not complete"
    return
  }
  # 安装器把 docker.exe 加进机器 PATH——刷新当前会话
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
              [Environment]::GetEnvironmentVariable("Path", "User")
  if (-not (Test-Command docker)) {
    Halt "Docker 已安装但 docker 命令不可用：请重启电脑后重试 / docker CLI unavailable — reboot and re-run"
    return
  }
}
Say "Docker 已安装 / Docker installed"

# ── 步骤 3/4：启动 Docker 引擎并等待就绪 ────────────────────────────────
if (-not (Test-DockerEngine)) {
  Say "启动 Docker Desktop 并等待引擎（最多 5 分钟）/ starting Docker engine (up to 5 min)"
  if (Test-Path $DockerDesktopExe) { Start-Process $DockerDesktopExe }
  $ready = $false
  foreach ($i in 1..60) {
    if (Test-DockerEngine) { $ready = $true; break }
    Info ("等待 Docker 引擎… / waiting… {0}s" -f ($i * 5))
    Start-Sleep -Seconds 5
  }
  if (-not $ready) {
    Halt "Docker 引擎未就绪：请手动打开 Docker Desktop（首次需接受协议）后重新运行 / engine not ready — open Docker Desktop manually (accept license on first run), then re-run"
    return
  }
}
Say "Docker 引擎就绪 / Docker engine ready"

# ── 步骤 4/4：部署 SinkDuce ─────────────────────────────────────────────
Say "创建目录并下载 compose / creating $Dir + compose"
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
try {
  Invoke-WebRequest "$Base/docker-compose.yml" `
    -OutFile (Join-Path $Dir "docker-compose.yml") -UseBasicParsing
} catch {
  Halt "compose 下载失败，请检查网络 / failed to download docker-compose.yml"
  return
}

Say "拉取镜像（约 1-2GB，请耐心等待）/ pulling images (about 1-2GB)"
Push-Location $Dir
& docker compose pull
$pullOk = ($LASTEXITCODE -eq 0)
if ($pullOk) {
  & docker compose up -d
}
Pop-Location
if (-not $pullOk) {
  Halt "镜像拉取失败，请检查网络后重新运行 / image pull failed — check network and re-run"
  return
}
if ($LASTEXITCODE -ne 0) {
  Halt "容器启动失败 / failed to start containers"
  return
}

Start-Sleep -Seconds 6
Start-Process "http://localhost:18900"
Write-Host ""
Write-Host "✅ SinkDuce 已运行：http://localhost:18900" -ForegroundColor Green
Write-Host "✅ SinkDuce is running: http://localhost:18900" -ForegroundColor Green
Info "首次进入请在页面里下载本地模型 / first run: download local models in the UI"
Info "以后更新 = 重新运行本命令 / to update later, re-run this same command"
