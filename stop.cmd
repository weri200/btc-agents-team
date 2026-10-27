@echo off
rem Stops the local server listening on port 8876 (or the port given: stop.cmd 8877).
rem start.cmd's wrapper window is closed first, or it would report the killed server as a crash.
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8876"
powershell -NoProfile -Command ^
  "$ids = (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique;" ^
  "if (-not $ids) { 'No server is running on port %PORT%.'; exit };" ^
  "foreach ($id in $ids) {" ^
  "  $p = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $id);" ^
  "  if ($p.Name -notlike 'python*') { 'Port %PORT% belongs to ' + $p.Name + ', not the server; left running.'; continue };" ^
  "  $wrapper = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $p.ParentProcessId);" ^
  "  if ($wrapper.CommandLine -like '*start.cmd*server*') { Stop-Process -Id $wrapper.ProcessId -Force };" ^
  "  Stop-Process -Id $id -Force; 'Stopped the server on port %PORT%.'" ^
  "}"
timeout /t 2 /nobreak >nul
