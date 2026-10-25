# Asks the installed Excel what its built-in table styles are made of. A hidden instance of its
# own, a blank workbook that is never saved; nothing of the person's is opened.
# Usage: dump-table-styles.ps1 -Out <table_styles.json>   (then gen-table-styles.py)
param([string]$Out)
$ErrorActionPreference = 'Stop'

function Hex([object]$bgr) {
  if ($null -eq $bgr -or $bgr -is [DBNull]) { return $null }
  $n = [int64]$bgr
  if ($n -lt 0) { return $null }
  '{0:X2}{1:X2}{2:X2}' -f ($n -band 255), (($n -shr 8) -band 255), (($n -shr 16) -band 255)
}
function Read([scriptblock]$get) {
  try { $v = & $get; if ($null -eq $v -or $v -is [DBNull]) { $null } else { $v } } catch { $null }
}
function Colour($holder) {
  # A colour as Excel holds it: by theme and tint when it has a theme, and what it comes to.
  $index = Read { $holder.ColorIndex }
  if ($index -eq -4142) { return $null }
  [ordered]@{
    rgb   = Hex (Read { $holder.Color })
    theme = Read { $holder.ThemeColor }
    tint  = Read { $holder.TintAndShade }
    index = $index
  }
}

$names = @('wholeTable', 'headerRow', 'totalRow', 'firstColumn', 'lastColumn', 'firstRowStripe', 'secondRowStripe', 'firstColumnStripe', 'secondColumnStripe', 'firstHeaderCell', 'lastHeaderCell', 'firstTotalCell', 'lastTotalCell')
$edges = [ordered]@{ left = 7; top = 8; bottom = 9; right = 10; vertical = 11; horizontal = 12 }

$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false
$xl.DisplayAlerts = $false
$wb = $null
try {
  $wb = $xl.Workbooks.Add()
  $theme = @()
  for ($i = 1; $i -le 12; $i++) { $theme += (Hex $wb.Theme.ThemeColorScheme.Colors($i).RGB) }
  $styles = [ordered]@{}
  foreach ($style in $wb.TableStyles) {
    $name = $style.Name
    if ($name -notlike 'TableStyle*') { continue }
    $parts = [ordered]@{}
    for ($e = 0; $e -le 12; $e++) {
      $el = $style.TableStyleElements.Item($e)
      if (-not (Read { $el.HasFormat })) { continue }
      $part = [ordered]@{}
      $font = Colour $el.Font
      $bold = Read { $el.Font.Bold }
      $italic = Read { $el.Font.Italic }
      if ($font) { $part.font = $font }
      if ($bold -eq $true) { $part.bold = $true }
      if ($italic -eq $true) { $part.italic = $true }
      $fill = Colour $el.Interior
      if ($fill) { $part.fill = $fill; $part.pattern = Read { $el.Interior.Pattern } }
      foreach ($edge in $edges.Keys) {
        $border = $el.Borders.Item($edges[$edge])
        $line = Read { $border.LineStyle }
        if ($null -eq $line -or $line -eq -4142) { continue }
        $part[$edge] = [ordered]@{ line = $line; weight = (Read { $border.Weight }); colour = (Colour $border) }
      }
      $size = Read { $el.StripeSize }
      if ($size -and $size -ne 1) { $part.size = $size }
      $parts[$names[$e]] = $part
    }
    $styles[$name] = $parts
  }
  [ordered]@{ excel = $xl.Version; theme = $theme; styles = $styles } | ConvertTo-Json -Depth 8 | Out-File -Encoding utf8 $Out
  "styles: $($styles.Count)"
}
finally {
  if ($wb) { $wb.Close($false) }
  $xl.Quit()
  [void][Runtime.InteropServices.Marshal]::ReleaseComObject($xl)
}
