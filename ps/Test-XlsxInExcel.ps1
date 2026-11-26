<#
.SYNOPSIS
  Opens every .xlsx in a folder in real Excel and checks it opens cleanly
  (no "We found a problem with some content" repair) and shows the expected values.

.DESCRIPTION
  Made for the cases written by python\xlsx_excel_cases.py (Mnemerynne's cell-level
  spreadsheet editing, MN-21). Each <name>.xlsx may have <name>.expect.json:
  {"Sheet!A1": value}. Excel opens the file read-only, recalculates, and the
  script compares each expected cell. A repair shows up as an error on open or
  as a new error*.xml repair log in %TEMP%.

.EXAMPLE
  .\ps\Test-XlsxInExcel.ps1 -Folder C:\temp\xlsx-excel-cases
#>
param([Parameter(Mandatory)] [string] $Folder)

$ErrorActionPreference = 'Stop'
$excel = New-Object -ComObject Excel.Application          # a hidden Excel just for this check
$excel.Visible = $false
$excel.DisplayAlerts = $false                              # never block on a dialog
$failed = 0
try {
    foreach ($file in Get-ChildItem -Path $Folder -Filter *.xlsx | Sort-Object Name) {
        $logsBefore = @(Get-ChildItem $env:TEMP -Filter 'error*.xml' -ErrorAction SilentlyContinue).Count   # Excel repair logs so far
        $problems = @()
        try {
            # Open(path, UpdateLinks=0, ReadOnly=true); the default load is "normal", which refuses
            # to open a damaged file rather than silently repairing it
            $wb = $excel.Workbooks.Open($file.FullName, 0, $true)
        } catch {
            Write-Output "FAIL $($file.Name): Excel couldn't open it ($($_.Exception.Message))"
            $failed++
            continue
        }
        $excel.CalculateFull()                              # formulas recalculated like on a real open
        $expectFile = [IO.Path]::ChangeExtension($file.FullName, '.expect.json')
        if (Test-Path $expectFile) {
            $expect = Get-Content $expectFile -Raw | ConvertFrom-Json
            foreach ($prop in $expect.PSObject.Properties) {
                $sheetName, $cell = $prop.Name -split '!', 2      # "Phone List!G3" -> sheet, cell
                $got = $wb.Worksheets.Item($sheetName).Range($cell).Value2
                if ("$got" -ne "$($prop.Value)") { $problems += "$($prop.Name) is '$got', expected '$($prop.Value)'" }
            }
        }
        $wb.Close($false)                                   # never save anything back
        $logsAfter = @(Get-ChildItem $env:TEMP -Filter 'error*.xml' -ErrorAction SilentlyContinue).Count
        if ($logsAfter -gt $logsBefore) { $problems += 'Excel wrote a repair log' }
        if ($problems) {
            Write-Output "FAIL $($file.Name): $($problems -join '; ')"
            $failed++
        } else {
            Write-Output "ok   $($file.Name)"
        }
    }
} finally {
    $excel.Quit()                                           # close the hidden Excel
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
}
if ($failed) { Write-Output "$failed file(s) failed"; exit 1 }
Write-Output 'All files opened cleanly in Excel.'
exit 0
