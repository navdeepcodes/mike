# Removes Mike from Windows: shortcuts and his private Python environment.
# Your data (memory, keys, settings in data\) is kept; set MIKE_REMOVE_DATA=1 to delete everything.
$Dir = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path $env:LOCALAPPDATA "Mike" }
Write-Host "-> Stopping Mike"
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*$Dir*server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "-> Removing shortcuts"
foreach ($f in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Startup"))) {
    Remove-Item (Join-Path $f "Mike.lnk"), (Join-Path $f "Uninstall Mike.lnk") -ErrorAction SilentlyContinue
}
Write-Host "-> Removing Mike's Python environment"
Remove-Item (Join-Path $Dir ".venv") -Recurse -Force -ErrorAction SilentlyContinue
if ($env:MIKE_REMOVE_DATA -eq "1") {
    Remove-Item $Dir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Mike and all his data are removed." -ForegroundColor Green
} else {
    Write-Host "Mike is uninstalled. Your memory, keys and settings are still in $Dir\data (delete the folder to remove them)." -ForegroundColor Green
}
