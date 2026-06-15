#!/usr/bin/env python3
import hashlib
import json
import os
import urllib.request

ACCOUNT = os.environ["REDMOND_ACCOUNT"]
PASSWORD = os.environ["REDMOND_PASSWORD"]
CALLING_CODE = os.getenv("REDMOND_CALLING_CODE", "65")
AUTH_URL = os.getenv("REDMOND_AUTH_URL", "https://user.grit-cloud.com/prod/oauth")

def post(url, payload, headers=None):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response: return json.load(response)

login = post(AUTH_URL, {"payload": {"opt": "login", "pwd": hashlib.md5(PASSWORD.encode()).hexdigest()}, "header": {"language": "en", "app_name": "Redmond", "calling_code": f"00{CALLING_CODE}", "api_version": "1.0", "account": ACCOUNT, "client_id": "yugong_app"}})
if login.get("msg") != "success": raise SystemExit(f"Login failed: {login.get('msg')}")
data = login["data"]
result = post(data["api_url"], {"opt": "thing_list_get"}, {"Token": data["jwt_token"], "Region": data["region_name"]})
if result.get("msg") != "success": raise SystemExit(f"Device query failed: {result.get('msg')}")
for thing in result.get("data", {}).get("thing_list", []):
    print(json.dumps({"nickname": thing.get("thing_nickname"), "thing_name": thing.get("thing_name"), "sub_type": thing.get("sub_type")}, ensure_ascii=False))
