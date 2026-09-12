# Render に見本の JSON を1件送る（TradingView をつなぐ前の配管確認用）。
# Python は要らない。Windows 標準の PowerShell だけで動く。
#
# 使い方:
#   powershell -ExecutionPolicy Bypass -File "<このファイルのフルパス>" https://tv-range-webhook.onrender.com
#
# 繰り返すときは末尾に -New を付ける（同じ内容は Render が重複として無視するため、
# 日付を今日に置き換えて別の1件として送る）。
#
# このファイルは BOM 付き UTF-8 で保存すること。
# BOM が無いと Windows PowerShell 5.1 は Shift-JIS として読み、日本語が化けて構文エラーになる。

param(
  [Parameter(Mandatory = $true)][string]$BaseUrl,
  [switch]$New
)

$ErrorActionPreference = 'Stop'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$base = $BaseUrl.TrimEnd('/')
$file = Join-Path $PSScriptRoot 'sample_payload.json'

if (-not (Test-Path $file)) {
  Write-Host ('sample_payload.json が見つかりません: ' + $file) -ForegroundColor Red
  exit 1
}

# JSON は解析せず、文字列のまま送る（数値の丸めや入れ子の崩れを避けるため）
$json = Get-Content -LiteralPath $file -Raw -Encoding UTF8

if ($New) {
  $today = (Get-Date).ToString('yyyy-MM-dd')
  $sid = ''
  if ($json -match '"symbol"\s*:\s*"([^"]+)"') { $sid = $Matches[1] + '_' + $today.Replace('-','') + '_LONG' }
  $json = [regex]::Replace($json, '"bar_date"\s*:\s*"[^"]*"', '"bar_date": "' + $today + '"')
  if ($sid -ne '') {
    $json = [regex]::Replace($json, '"signal_id"\s*:\s*"[^"]*"', '"signal_id": "' + $sid + '"')
  }
  Write-Host ('日付を ' + $today + ' に置き換えて送ります  signal_id = ' + $sid)
}

$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
$url = $base + '/webhook/tradingview'
Write-Host ('送信先: ' + $url)

try {
  $res = Invoke-WebRequest -Uri $url -Method Post -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec 60 -UseBasicParsing
  Write-Host ('応答 ' + $res.StatusCode + ': ' + $res.Content) -ForegroundColor Green
  Write-Host ''
  Write-Host '次の確認:'
  Write-Host '  1. Discord の「レンジ-即時」に画像付きで1件届いているか'
  Write-Host ('  2. ' + $base + '/signals/recent をブラウザで開いて、行が見えるか')
  Write-Host '  3. 届かないときは Render の画面左の Logs に理由が出ている'
}
catch {
  Write-Host '失敗しました。' -ForegroundColor Red
  Write-Host $_.Exception.Message
  if ($_.Exception.Response) {
    try {
      $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
      Write-Host ('本文: ' + $sr.ReadToEnd())
    } catch { }
  }
  Write-Host ''
  Write-Host 'よくある原因:'
  Write-Host '  1. URL の綴り違い。https:// から始まっているか、末尾に余分な文字がないか'
  Write-Host ('  2. Render が眠っている。' + $base + '/ を一度ブラウザで開いてから、もう一度実行する')
  Write-Host '  3. Deploy が失敗している。Render の Events と Logs を見る'
  exit 1
}
