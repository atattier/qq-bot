# 重启 bot。
#
# 2026-10-25 踩的两个坑：
# 1) 老脚本正则 'qq-bot\bot\.py' 匹配不上真实命令行（`python.exe -u bot.py`，不含目录），
#    老进程杀不掉，新进程被 state/bot.lock 挡住退出 —— 看起来"重启了"其实没重启。
# 2) 项目里跑着 _keeper.py 保活（PID 在 state/keeper.pid），它 10 秒后会把 bot 自己拉起来。
#    手动 Start-Process 一个 bot 只会和 keeper 抢锁、来回抖。
#
# 所以正确做法：**只杀 bot.py 这个子进程，让 keeper 去重启**；keeper 没在跑才手动拉。
$botDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = python
$lockFile = Join-Path $botDir "state\bot.lock"
$keeperPidFile = Join-Path $botDir "state\keeper.pid"

function Get-BotProcs {
  Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine -match 'bot\.py' -and $_.CommandLine -notmatch '_keeper'
  }
}

# 1) 杀 bot（含锁文件里记的 PID，双保险）
$targets = @()
$p = Get-BotProcs
foreach ($x in $p) { $targets += [int]$x.ProcessId }
if (Test-Path $lockFile) {
  $raw = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($raw -match '^\s*(\d+)\s*$') { $targets += [int]$Matches[1] }
}
$targets = $targets | Sort-Object -Unique
foreach ($tp in $targets) {
  if ($tp -eq $PID) { continue }
  $proc = Get-Process -Id $tp -ErrorAction SilentlyContinue
  if ($proc -and $proc.ProcessName -match '^python') {
    Write-Output ("kill bot pid " + $tp)
    Stop-Process -Id $tp -Force -ErrorAction SilentlyContinue
  }
}
Start-Sleep -Seconds 2
if (Test-Path $lockFile) { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue }

# 2) keeper 在跑就交给它；不在跑就自己拉一个
$keeperAlive = $false
if (Test-Path $keeperPidFile) {
  $kp = (Get-Content $keeperPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($kp -match '^\s*(\d+)\s*$') {
    $kproc = Get-Process -Id ([int]$Matches[1]) -ErrorAction SilentlyContinue
    if ($kproc) { $keeperAlive = $true; Write-Output ("keeper 存活 pid " + $Matches[1] + "，等它重启 bot……") }
  }
}

if ($keeperAlive) {
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if ((Get-BotProcs | Measure-Object).Count -gt 0) { Write-Output "bot 已由 keeper 拉起"; break }
  }
} else {
  Write-Output "keeper 没在跑，手动启动 bot"
  Start-Process -FilePath $py -ArgumentList "-u", "bot.py" -WorkingDirectory $botDir -WindowStyle Hidden
  Start-Sleep -Seconds 6
}

if ((Get-BotProcs | Measure-Object).Count -gt 0) { Write-Output "OK: bot 在跑" }
else { Write-Output "FAIL: bot 没起来，看 state\keeper.log 和 bot.log" }
