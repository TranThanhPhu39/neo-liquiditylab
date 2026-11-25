param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
$taskName = 'LiquidityLab - GitHub Realtime Dispatch Guard'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonWindowlessPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$launcherArguments = '-m scripts.scheduled_cloud_guard_entry'
if ((Get-TimeZone).Id -ne 'SE Asia Standard Time') { throw 'Scheduler expects SE Asia Standard Time.' }
if (-not (Test-Path -LiteralPath $pythonWindowlessPath)) { throw 'Project Python is missing.' }
if (-not $VerifyOnly) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions[0].Arguments -notin @($launcherArguments,'-m scripts.scheduled_cloud_guard') -or $existing.Actions[0].WorkingDirectory -ne $projectRoot)) {
        throw 'Existing task belongs to another launcher; refusing overwrite.'
    }
    $userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $pythonWindowlessPath -Argument $launcherArguments -WorkingDirectory $projectRoot
    $weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '08:15'
    $pattern = New-ScheduledTaskTrigger -Once -At '08:15' -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 7 -Minutes 20)
    $weekly.Repetition = $pattern.Repetition
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $userId
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($weekly,$logon) -Principal $principal -Settings $settings -Description 'Independent Windows timer checks SSI/DNSE GitHub collectors every 5 minutes on weekdays, 08:15-15:35 Vietnam and at logon. Bounded retries, no duplicate active jobs, daily encrypted DNSE catalog refresh. Requires this PC on and user logged in; no stored task password or trading.' -Force | Out-Null
}
$task = Get-ScheduledTask -TaskName $taskName
$info = $task | Get-ScheduledTaskInfo
$report = [ordered]@{
    task_name=$taskName; state=[string]$task.State; timezone=(Get-TimeZone).Id;
    verified_at_local=(Get-Date).ToString('o'); next_run_local=$info.NextRunTime.ToString('o');
    execute=$task.Actions[0].Execute; arguments=$task.Actions[0].Arguments;
    repetition_interval=$task.Triggers[0].Repetition.Interval; repetition_duration=$task.Triggers[0].Repetition.Duration;
    logon_type=[string]$task.Principal.LogonType; last_task_result=$info.LastTaskResult;
    start_when_available=$task.Settings.StartWhenAvailable; multiple_instances=[string]$task.Settings.MultipleInstances;
    requires_computer_on_and_user_logged_in=$true; no_stored_password=$true;
    stop_file='data/control/STOP_GITHUB_DISPATCH'
}
$target = Join-Path $projectRoot 'report\automation\cloud_dispatch_task_registration.json'
New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
$report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $target -Encoding UTF8
$report | ConvertTo-Json -Depth 6
