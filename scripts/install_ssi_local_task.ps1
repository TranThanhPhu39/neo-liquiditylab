param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$taskName = 'LiquidityLab - SSI Local Session Fallback'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonWindowlessPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$launcherArguments = '-m scripts.scheduled_ssi_local'
if ((Get-TimeZone).Id -ne 'SE Asia Standard Time') { throw 'Scheduler expects SE Asia Standard Time.' }
if (-not (Test-Path -LiteralPath $pythonWindowlessPath)) { throw 'Project Python is missing.' }
if (-not $VerifyOnly) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions[0].Arguments -ne $launcherArguments -or $existing.Actions[0].WorkingDirectory -ne $projectRoot)) { throw 'Existing task belongs to another launcher.' }
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $pythonWindowlessPath -Argument $launcherArguments -WorkingDirectory $projectRoot
    $weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '08:52'
    $pattern = New-ScheduledTaskTrigger -Once -At '08:52' -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 20)
    $weekly.Repetition = $pattern.Repetition
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($weekly,$logon) -Principal $principal -Settings $settings -Description 'SSI local market-data fallback. Checks every 5 minutes from 08:52 through 15:12. Records only if cloud start/data proof is missing, keeps Parquet locally, uploads encrypted checkpoints, never competes with the live relay. Requires this PC on and user logged in. No trading or stored password.' -Force | Out-Null
}
$task = Get-ScheduledTask -TaskName $taskName
$info = $task | Get-ScheduledTaskInfo
$report = [ordered]@{
    task_name=$taskName;state=[string]$task.State;verified_at_local=(Get-Date).ToString('o');
    next_run_local=$info.NextRunTime.ToString('o');last_task_result=$info.LastTaskResult;
    execute=$task.Actions[0].Execute;arguments=$task.Actions[0].Arguments;
    repetition_interval=$task.Triggers[0].Repetition.Interval;repetition_duration=$task.Triggers[0].Repetition.Duration;
    logon_type=[string]$task.Principal.LogonType;no_stored_password=$true;
    requires_computer_on_and_user_logged_in=$true;stop_file='data/control/STOP_SSI_LOCAL'
}
$target = Join-Path $projectRoot 'report\automation\ssi_local_task_registration.json'
New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $target -Encoding UTF8
$report | ConvertTo-Json -Depth 6
