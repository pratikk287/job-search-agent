# Creates a "Job Search Agent" shortcut on your Desktop that starts the app.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:USERPROFILE\Desktop\Job Search Agent.lnk")
$s.TargetPath = Join-Path $here "Start Job Search Agent.bat"
$s.WorkingDirectory = $here
$s.WindowStyle = 7
$s.Save()
Write-Host "Shortcut created on your Desktop."
