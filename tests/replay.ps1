# TradingView のログに残った JSON を、あとから Render に投げ直す。
#
# 使い方:
#   1. TradingView の「ログ」で失敗した通知を開き、JSON を1件ずつコピーして
#      テキストファイルに貼る。1行に1件。空行があっても構わない。
#      例: P:\400.コーディング\TradingView\20260910レンジ\v7\replay.txt
#   2. PowerShell で
#      .\tests\replay.ps1 https://tv-range-webhook.onrender.com "P:\...\replay.txt"
#
# 同じ signal_id は Render が重複として無視するので、二度流しても増えません。
#
# このファイルは BOM 付き UTF-8 で保存すること。

param(
  [Parameter(Mandatory = $true)][string]$BaseUrl,
  [Parameter(Mandatory = $true)][string]$File
)

$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$url = $BaseUrl.TrimEnd('/') + '/webhook/tradingview'
if (-not (Test-Path -LiteralPath $File)) {
  Write-Host ('ファイルが見つかりません: ' + $File) -ForegroundColor Red
  exit 1
}

# まず Render を起こす。眠っていると最初の1件を落とすため。
Write-Host 'Render を起こしています（最大90秒）...'
try {
  Invoke-WebRequest -Uri ($BaseUrl.TrimEnd('/') + '/') -TimeoutSec 90 -UseBasicParsing | Out-Null
  Write-Host '起きました。' -ForegroundColor Green
} catch {
  Write-Host '起こせませんでしたが、そのまま続けます。' -ForegroundColor Yellow
}

$lines = Get-Content -LiteralPath $File -Encoding UTF8 | Where-Object { $_.Trim() -ne '' }
Write-Host ('送る件数: ' + $lines.Count)

$ok = 0
$ng = 0
foreach ($line in $lines) {
  $body = $line.Trim()
  if (-not $body.StartsWith('{')) {
    Write-Host ('JSON に見えないので飛ばします: ' + $body.Substring(0, [Math]::Min(40, $body.Length))) -ForegroundColor Yellow
    continue
  }
  $sym = ''
  if ($body -match '"symbol"\s*:\s*"([^"]+)"') { $sym = $Matches[1] }
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    $res = Invoke-WebRequest -Uri $url -Method Post -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 60 -UseBasicParsing
    Write-Host ('  ' + $sym + ' -> ' + $res.StatusCode) -ForegroundColor Green
    $ok = $ok + 1
  } catch {
    Write-Host ('  ' + $sym + ' -> 失敗: ' + $_.Exception.Message) -ForegroundColor Red
    $ng = $ng + 1
  }
  Start-Sleep -Seconds 3
}

Write-Host ''
Write-Host ('成功 ' + $ok + ' 件 / 失敗 ' + $ng + ' 件')
Write-Host 'Discord の「レンジ-即時」と、' + $BaseUrl.TrimEnd('/') + '/signals/recent を確認してください。'
