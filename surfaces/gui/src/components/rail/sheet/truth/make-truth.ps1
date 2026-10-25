# Has the installed Excel make a workbook (tables in every built-in style, conditional
# formatting, a picture) and say how it shows each cell: the ground truth the sheet view is
# compared with. A hidden instance of its own; the only files touched are the two it is told
# to write. Invented data.
# Usage: make-truth.ps1 -Out <dir> -Picture <any small png>
# It writes truth.xlsx and truth.json there. Before they are kept as the test's files: take the
# author out of docProps/core.xml and the x15ac:absPath block out of xl/workbook.xml (the name
# and folder of whoever ran this), base64 the workbook into excel.xlsx.b64.txt, and keep the
# JSON's `excel`, `tables` and `rules` as excel.json.
param([string]$Out, [string]$Picture)
$ErrorActionPreference = 'Stop'
$missing = [Type]::Missing

function Hex([object]$bgr) {
  if ($null -eq $bgr -or $bgr -is [DBNull]) { return $null }
  $n = [int64]$bgr
  '{0:X2}{1:X2}{2:X2}' -f ($n -band 255), (($n -shr 8) -band 255), (($n -shr 16) -band 255)
}
function Bgr([string]$hex) { [Convert]::ToInt32($hex.Substring(4, 2) + $hex.Substring(2, 2) + $hex.Substring(0, 2), 16) }
function Edge($borders, [int]$which) {
  $b = $borders.Item($which)
  $line = $b.LineStyle
  if ($null -eq $line -or $line -is [DBNull] -or $line -eq -4142) { return $null }
  $kind = if ($line -eq -4119) { 'double' } elseif ($b.Weight -eq 4) { 'thick' } elseif ($b.Weight -eq -4138) { 'medium' } elseif ($b.Weight -eq 1) { 'hair' } else { 'thin' }
  "$kind`:$(Hex $b.Color)"
}
function Shown($cell) {
  $d = $cell.DisplayFormat
  $fill = if ($d.Interior.ColorIndex -eq -4142) { $null } else { Hex $d.Interior.Color }
  # row, column (from 0), fill, type colour, bold, then the four edges
  , @(($cell.Row - 1), ($cell.Column - 1), $fill, (Hex $d.Font.Color), [bool]$d.Font.Bold, (Edge $d.Borders 8), (Edge $d.Borders 7), (Edge $d.Borders 9), (Edge $d.Borders 10))
}

$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
$wb = $null
try {
  $wb = $xl.Workbooks.Add()
  while ($wb.Worksheets.Count -gt 1) { $wb.Worksheets.Item($wb.Worksheets.Count).Delete() }

  # --- tables: every built-in style, twice, with different parts switched on -------------
  $ws = $wb.Worksheets.Item(1)
  $ws.Name = 'Tables'
  $names = @()
  foreach ($style in $wb.TableStyles) { if ($style.Name -like 'TableStyle*') { $names += $style.Name } }
  $tables = @()
  $n = 0
  foreach ($name in $names) {
    foreach ($half in 0, 1) {
      # Sets 0 and 1 for one style, 2 and 3 for the next: neighbours differ in colour only.
      $set = ($n % 2) * 2 + $half
      $t = $n * 2 + $half
      $top = 1 + [math]::Floor($t / 6) * 9
      $left = 1 + ($t % 6) * 5
      for ($c = 0; $c -lt 3; $c++) {
        $ws.Cells.Item($top, $left + $c).Value2 = "H$($c + 1)"
        for ($r = 1; $r -le 4; $r++) { $ws.Cells.Item($top + $r, $left + $c).Value2 = ($r * 10 + $c) }
      }
      $range = $ws.Range($ws.Cells.Item($top, $left), $ws.Cells.Item($top + 4, $left + 2))
      $lo = $ws.ListObjects.Add(1, $range, $missing, 1)
      $lo.TableStyle = $name
      $lo.ShowAutoFilter = $false
      switch ($set) {
        0 { }
        1 { $lo.ShowTableStyleFirstColumn = $true; $lo.ShowTableStyleLastColumn = $true; $lo.ShowTotals = $true }
        2 { $lo.ShowTableStyleColumnStripes = $true; $lo.ShowTableStyleRowStripes = $false }
        3 { $lo.ShowHeaders = $false; $lo.ShowTotals = $true }
      }
      $cells = @()
      foreach ($cell in $lo.Range.Cells) { $cells += (Shown $cell) }
      $tables += [ordered]@{ style = $name; set = $set; ref = $lo.Range.Address($false, $false); cells = $cells }
    }
    $n++
  }

  # --- conditional formatting: one kind of rule a column ----------------------------------
  $rs = $wb.Worksheets.Add()
  $rs.Name = 'Rules'
  $heads = 'less', 'between', 'greater-mixed', 'contains', 'top3', 'above', 'duplicate', 'scale3', 'scale2', 'bar', 'bottom20', 'equal-text', 'blank', 'two-rules', 'begins', 'not-equal'
  for ($c = 0; $c -lt $heads.Count; $c++) { $rs.Cells.Item(1, $c + 1).Value2 = [string]$heads[$c] }
  $numbers = 7, 2, 9, 4, 4, 10, 1, 6, 3, 8
  $words = 'cab', 'dog', 'Abacus', 'bee', 'slab', 'yes', 'no', 'YES', 'alpha', 'abc'
  for ($r = 0; $r -lt 10; $r++) {
    foreach ($c in 1, 2, 5, 6, 7, 8, 9, 10, 11, 14, 16) { $rs.Cells.Item($r + 2, $c).Value2 = [double]$numbers[$r] }
    foreach ($c in 4, 12, 15) { $rs.Cells.Item($r + 2, $c).Value2 = [string]$words[$r] }
    $rs.Cells.Item($r + 2, 3).Value2 = [double]$numbers[$r]
    $rs.Cells.Item($r + 2, 13).Value2 = [double]$numbers[$r]
  }
  # a text and an empty cell among numbers, and two empty cells where blanks are looked for
  $rs.Cells.Item(4, 3).Value2 = 'n/a'
  $rs.Cells.Item(6, 3).ClearContents() | Out-Null
  $rs.Cells.Item(5, 13).ClearContents() | Out-Null
  $rs.Cells.Item(9, 13).ClearContents() | Out-Null
  # an empty cell and a text among numbers that are compared
  foreach ($c in 1, 2, 16) { $rs.Cells.Item(7, $c).ClearContents() | Out-Null; $rs.Cells.Item(8, $c).Value2 = 'n/a' }
  $red = Bgr 'FFC7CE'; $green = Bgr 'C6EFCE'; $ink = Bgr '9C0006'

  $f = $rs.Range("A2:A11").FormatConditions.Add(1, 6, '4'); $f.Interior.Color = $red
  $f = $rs.Range("B2:B11").FormatConditions.Add(1, 1, '4', '8'); $f.Interior.Color = $green; $f.Font.Bold = $true
  $f = $rs.Range("C2:C11").FormatConditions.Add(1, 5, '5'); $f.Interior.Color = $red
  $f = $rs.Range("D2:D11").FormatConditions.Add(9, $missing, $missing, $missing, 'ab', 0); $f.Interior.Color = $green
  $f = $rs.Range("E2:E11").FormatConditions.AddTop10(); $f.TopBottom = 1; $f.Rank = 3; $f.Interior.Color = $red
  $f = $rs.Range("F2:F11").FormatConditions.AddAboveAverage(); $f.AboveBelow = 0; $f.Interior.Color = $green
  $f = $rs.Range("G2:G11").FormatConditions.AddUniqueValues(); $f.DupeUnique = 1; $f.Interior.Color = $red
  [void]$rs.Range("H2:H11").FormatConditions.AddColorScale(3)
  [void]$rs.Range("I2:I11").FormatConditions.AddColorScale(2)
  [void]$rs.Range("J2:J11").FormatConditions.AddDatabar()
  $f = $rs.Range("K2:K11").FormatConditions.AddTop10(); $f.TopBottom = 0; $f.Rank = 20; $f.Percent = $true; $f.Interior.Color = $green
  $f = $rs.Range("L2:L11").FormatConditions.Add(1, 3, '="yes"'); $f.Interior.Color = $green
  $f = $rs.Range("M2:M11").FormatConditions.Add(10); $f.Interior.Color = $red
  $f = $rs.Range("N2:N11").FormatConditions.Add(1, 5, '5'); $f.Font.Color = $ink; $f.Font.Bold = $true
  $f = $rs.Range("N2:N11").FormatConditions.Add(1, 5, '8'); $f.Interior.Color = $red; $f.Font.Color = (Bgr '0000FF')
  $f = $rs.Range("O2:O11").FormatConditions.Add(9, $missing, $missing, $missing, 'a', 2); $f.Interior.Color = $red
  $f = $rs.Range("P2:P11").FormatConditions.Add(1, 4, '4'); $f.Interior.Color = $green
  $rules = @()
  foreach ($cell in $rs.Range($rs.Cells.Item(2, 1), $rs.Cells.Item(11, $heads.Count)).Cells) { $rules += (Shown $cell) }

  # --- a picture, a table with data around it, frozen panes ---------------------------------
  $ps = $wb.Worksheets.Add()
  $ps.Name = 'Picture'
  $ps.Cells.Item(1, 1).Value2 = 'A picture sits at C3'
  $shape = $ps.Shapes.AddPicture($Picture, $false, $true, $ps.Cells.Item(3, 3).Left + 6, $ps.Cells.Item(3, 3).Top + 4, 120, 60)
  $shape.AlternativeText = 'A test card'
  $picture = [ordered]@{ left = $shape.Left; top = $shape.Top; width = $shape.Width; height = $shape.Height; cellLeft = $ps.Cells.Item(3, 3).Left; cellTop = $ps.Cells.Item(3, 3).Top }

  $ws.Activate()
  $file = Join-Path $Out 'truth.xlsx'
  if (Test-Path $file) { Remove-Item $file -Confirm:$false }
  $wb.SaveAs($file, 51)
  [ordered]@{ excel = $xl.Version; tables = $tables; rules = $rules; picture = $picture } | ConvertTo-Json -Depth 6 -Compress | Out-File -Encoding utf8 (Join-Path $Out 'truth.json')
  "tables: $($tables.Count)  rule cells: $($rules.Count)"
}
finally {
  if ($wb) { $wb.Close($false) }
  $xl.Quit()
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
}
