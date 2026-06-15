#!/usr/bin/env python3
import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.getenv("BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("BRIDGE_PORT", "18081"))
AUTH_URL = os.getenv("REDMOND_AUTH_URL", "https://user.grit-cloud.com/prod/oauth")
ACCOUNT = os.environ["REDMOND_ACCOUNT"]
PASSWORD = os.environ["REDMOND_PASSWORD"]
CALLING_CODE = os.getenv("REDMOND_CALLING_CODE", "65")
THING_NAME = os.environ["ABIR_THING_NAME"]
SUB_TYPE = os.getenv("ABIR_SUB_TYPE", "xs-x6")
API_TOKEN = os.environ["ROBOT_API_TOKEN"]

COMMANDS = {
    "clean": {"state": {"working_status": "AutoClean"}, "statuses": {"AutoClean"}},
    "stop": {"state": {"working_status": "Standby"}, "statuses": {"Standby", "Hibernating"}, "already_ok": {"Standby", "Hibernating", "Charging", "ChargeDone"}},
    "dock": {"state": {"working_status": "BackCharging"}, "statuses": {"BackCharging", "Charging", "ChargeDone", "PileCharging"}},
    "locate": {"state": {"working_status": "LocationAlarm"}, "accept_only": True},
    "fan_normal": {"state": {"fan_status": "Normal"}, "fan": "Normal"},
    "fan_strong": {"state": {"fan_status": "Strong"}, "fan": "Strong"},
}
session_lock = threading.Lock()
session = {}

def post_json(url, payload, headers=None):
    request_headers = {"Content-Type": "application/json", "User-Agent": "AbirX6Bridge/1.0"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

def login():
    response = post_json(AUTH_URL, {"payload": {"opt": "login", "pwd": hashlib.md5(PASSWORD.encode("utf-8")).hexdigest()}, "header": {"language": "en", "app_name": "Redmond", "calling_code": f"00{CALLING_CODE}", "api_version": "1.0", "account": ACCOUNT, "client_id": "yugong_app"}})
    if response.get("msg") != "success": raise RuntimeError(f"Redmond login failed: {response.get('msg')}")
    data = response["data"]
    session.update({"api_url": data["api_url"], "token": data["jwt_token"], "region": data["region_name"], "expires": time.time() + 3600})

def api_call(payload):
    with session_lock:
        if time.time() >= session.get("expires", 0): login()
        try: response = post_json(session["api_url"], payload, {"Token": session["token"], "Region": session["region"]})
        except urllib.error.HTTPError as error:
            if error.code not in {401, 403}: raise
            login(); response = post_json(session["api_url"], payload, {"Token": session["token"], "Region": session["region"]})
    if response.get("msg") != "success": raise RuntimeError(f"Redmond API error: {response.get('msg')}")
    return response.get("data", {})

def get_status():
    source = api_call({"opt": "thing_status_get", "sub_type": SUB_TYPE, "thing_name": THING_NAME}).get("thing_status", {})
    return {"connected": str(source.get("connected")).lower() == "true", "status": source.get("working_status", "Unknown"), "battery": source.get("battery_level"), "fan": source.get("fan_status"), "error": source.get("error_info", "Unknown"), "firmware": source.get("firmware_version"), "updated_at": int(time.time())}

def send_state(state):
    api_call({"topic_name": f"$aws/things/{THING_NAME}/shadow/update", "opt": "send_to_device", "sub_type": SUB_TYPE, "topic_payload": {"state": state}, "thing_name": THING_NAME})

def wait_for(spec, timeout=8):
    deadline = time.time() + timeout; latest = get_status()
    while time.time() < deadline:
        if latest.get("status") in spec.get("statuses", set()) or (spec.get("fan") and latest.get("fan") == spec["fan"]): return True, latest
        time.sleep(1); latest = get_status()
    return False, latest

def start_clean(before):
    latest = before
    for attempt in range(3):
        logging.info("Clean attempt %d from %s", attempt + 1, latest.get("status")); send_state({"working_status": "AutoClean"}); confirmed, latest = wait_for(COMMANDS["clean"], timeout=7)
        if confirmed:
            time.sleep(4); latest = get_status()
            if latest.get("status") == "AutoClean": return True, latest
    return False, latest

def return_to_dock(before):
    latest = before
    for attempt in range(2):
        logging.info("Dock attempt %d from %s", attempt + 1, latest.get("status"))
        if latest.get("status") == "Hibernating":
            send_state({"working_status": "AutoClean"}); woke, latest = wait_for(COMMANDS["clean"], timeout=7)
            if not woke: continue
            time.sleep(2)
        send_state({"working_status": "BackCharging"}); confirmed, latest = wait_for(COMMANDS["dock"], timeout=6)
        if confirmed:
            time.sleep(10); latest = get_status()
            if latest.get("status") in COMMANDS["dock"]["statuses"]: return True, latest
    return False, latest

def send_command(command):
    spec = COMMANDS[command]; before = get_status()
    if not before["connected"]: return False, "Robot is offline.", before
    if command == "clean":
        confirmed, latest = start_clean(before); return confirmed, "Robot started cleaning." if confirmed else "Robot did not start.", latest
    if command == "dock":
        confirmed, latest = return_to_dock(before); return confirmed, "Robot is returning to dock." if confirmed else "Robot did not remain in return-to-dock mode. It may be deeply asleep.", latest
    if before["status"] in spec.get("already_ok", set()): return True, "Robot is already in the requested state.", before
    send_state(spec["state"])
    if spec.get("accept_only"): time.sleep(1); return True, "Command was sent; this firmware may not perform it.", get_status()
    confirmed, latest = wait_for(spec); return confirmed, "Robot confirmed the command." if confirmed else "No confirmation.", latest

class Handler(BaseHTTPRequestHandler):
    server_version = "AbirX6Bridge/1.0"
    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8"); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def authorized(self): return self.headers.get("Authorization") == f"Bearer {API_TOKEN}"
    def do_GET(self):
        if self.path == "/health": self.reply(200, {"ok": True}); return
        if not self.authorized(): self.reply(401, {"ok": False, "error": "unauthorized"}); return
        if self.path != "/status": self.reply(404, {"ok": False, "error": "not_found"}); return
        try: self.reply(200, {"ok": True, "robot": get_status()})
        except Exception as error: self.reply(502, {"ok": False, "error": str(error)})
    def do_POST(self):
        if not self.authorized(): self.reply(401, {"ok": False, "error": "unauthorized"}); return
        if self.path != "/command": self.reply(404, {"ok": False, "error": "not_found"}); return
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")); command = request.get("command")
            if command not in COMMANDS: self.reply(400, {"ok": False, "error": "unknown_command"}); return
            confirmed, message, robot = send_command(command); self.reply(200, {"ok": confirmed, "command": command, "message": message, "robot": robot})
        except Exception as error: self.reply(502, {"ok": False, "error": str(error)})
    def log_message(self, fmt, *args): logging.info("%s %s", self.client_address[0], fmt % args)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
