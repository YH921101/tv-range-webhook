"""Render に見本の JSON を1件送る（TradingView をつなぐ前の配管確認用）。

使い方:  python -m tests.send_sample https://<service>.onrender.com
Discord と Turso がつながっていれば、即時チャンネルに1件届き、/signals/recent に行が見える。
同じ signal_id は二度目以降無視されるので、繰り返すときは --new を付ける（日付を今日に置き換える）。
"""
import json, os, sys, datetime, urllib.request

base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
path = os.path.join(os.path.dirname(__file__), "sample_payload.json")
payload = json.load(open(path, encoding="utf-8"))
if "--new" in sys.argv:
    today = datetime.date.today().strftime("%Y-%m-%d")
    payload["bar_date"] = today
    payload["signal_id"] = "%s_%s_LONG" % (payload["symbol"], today.replace("-", ""))
req = urllib.request.Request(base + "/webhook/tradingview", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=30) as r:
    print(r.status, r.read().decode("utf-8"))
print("sent signal_id =", payload["signal_id"])
