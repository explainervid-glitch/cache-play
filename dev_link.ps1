# Link Blender's installed Cache Play extension to this project folder, so code changes
# show up after restarting Blender, without building and installing a zip.
#
#   powershell -ExecutionPolicy Bypass -File dev_link.ps1            # Blender 5.2
#   powershell -ExecutionPolicy Bypass -File dev_link.ps1 -Version 5.1
#   powershell -ExecutionPolicy Bypass -File dev_link.ps1 -Remove     # put the backup back
#
# Needs Windows Developer Mode (symlinks without admin rights). Close Blender first, or
# restart it afterwards. While linked, do not use Uninstall or Update on Cache Play inside
# Blender: run this script with -Remove instead.
param(
    [string]$Version = "5.2",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$extensions = Join-Path $env:APPDATA "Blender Foundation\Blender\$Version\extensions\user_default"
$link = Join-Path $extensions "cache_play"
$backup = Join-Path $env:APPDATA "Blender Foundation\Blender\$Version\cache_play_installed_backup"

# Use the network path behind a mapped drive letter, so the link does not depend on Z: being mapped.
$source = Join-Path $PSScriptRoot "cache_play"
$drive = Split-Path -Qualifier $source
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'"
if ($disk -and $disk.ProviderName) {
    $source = $disk.ProviderName + $source.Substring($drive.Length)
}

function Is-Link($path) {
    (Test-Path $path) -and ((Get-Item $path -Force).LinkType -eq "SymbolicLink")
}

if ($Remove) {
    if (Is-Link $link) { (Get-Item $link -Force).Delete(); "Removed link $link" }
    if (Test-Path $backup) { Move-Item $backup $link; "Restored installed copy from $backup" }
    return
}

if (-not (Test-Path $extensions)) { throw "Blender $Version extensions folder not found: $extensions" }
if (Is-Link $link) {
    "Already linked: $link -> $((Get-Item $link -Force).Target)"
    return
}
if (Test-Path $link) {
    if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
    Move-Item $link $backup
    "Moved installed copy to $backup"
}
cmd /c mklink /D "$link" "$source" | Out-Host
if (-not (Test-Path (Join-Path $link "blender_manifest.toml"))) { throw "Link was not created" }
"Linked $link -> $source. Restart Blender $Version."
