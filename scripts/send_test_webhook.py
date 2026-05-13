import json
import urllib.request

payload = {"ProjectName": "ProjectA", "Customer": "ACME", "ProjectNumber": "PRJ-123"}
url = "http://127.0.0.1:5000/webhook/after-insert"
req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as resp:
        print(resp.status)
        print(resp.read().decode())
except Exception as e:
    print("ERROR:", e)
    try:
        import sys
        import traceback
        traceback.print_exc()
    except Exception:
        pass
