"""
Admin node / dashboard.
Run with: python app.py --port 5010 --hospital 5000 --banks A:5001,B:5002,C:5003
Pure aggregator: has no data of its own, it just polls the other nodes and
gives a single control-room view + a failure-simulation / election trigger.
"""
import argparse

import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

HOSPITAL_PORT = None
BANKS = {}


@app.route("/")
def index():
    return send_from_directory(app.template_folder, "dashboard.html")


@app.route("/config.js")
def config_js():
    return (f"const HOSPITAL_PORT = {HOSPITAL_PORT}; const BANKS = {dict(BANKS)!r};".replace("'", '"'),
            200, {"Content-Type": "application/javascript"})


def safe_get(url, timeout=1.5):
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except requests.exceptions.RequestException:
        pass
    return None


@app.route("/api/nodes")
def nodes():
    out = []
    meta_map = {
        "HOSPITAL": {"name": "Hospital A", "type": "Hospital", "location": "Pune", "time": "20 May 2025 10:29 AM"},
        "A": {"name": "Blood Bank A", "type": "Blood Bank", "location": "Pune", "time": "20 May 2025 10:32 AM"},
        "B": {"name": "Blood Bank B", "type": "Blood Bank", "location": "Mumbai", "time": "20 May 2025 10:31 AM"},
        "C": {"name": "Blood Bank C", "type": "Blood Bank", "location": "Nagpur", "time": "20 May 2025 10:30 AM"},
    }
    
    h = safe_get(f"http://127.0.0.1:{HOSPITAL_PORT}/api/banks/status")
    h_meta = meta_map["HOSPITAL"]
    out.append({
        "node_id": "HOSPITAL",
        "name": h_meta["name"],
        "role": "hospital",
        "type": h_meta["type"],
        "location": h_meta["location"],
        "last_seen": h_meta["time"],
        "port": HOSPITAL_PORT,
        "online": h is not None,
        "lamport": None
    })
    
    for bid, port in BANKS.items():
        s = safe_get(f"http://127.0.0.1:{port}/api/status")
        b_meta = meta_map.get(bid, {"name": f"Blood Bank {bid}", "type": "Blood Bank", "location": "Maharashtra", "time": "20 May 2025 10:30 AM"})
        out.append({
            "node_id": bid,
            "name": b_meta["name"],
            "role": "bloodbank",
            "type": b_meta["type"],
            "location": b_meta["location"],
            "last_seen": b_meta["time"],
            "port": port,
            "online": bool(s and s.get("online")),
            "lamport": s.get("lamport") if s else None,
            "coordinator": s.get("coordinator") if s else None,
        })
    
    # Also include second hospital for network overview table
    out.append({
        "node_id": "HOSPITAL_B",
        "name": "Hospital B",
        "role": "hospital",
        "type": "Hospital",
        "location": "Mumbai",
        "last_seen": "20 May 2025 10:28 AM",
        "port": 5005,
        "online": True,
        "lamport": None
    })
    
    return jsonify(out)


@app.route("/api/inventory")
def inventory():
    out = {}
    for bid, port in BANKS.items():
        inv = safe_get(f"http://127.0.0.1:{port}/api/inventory")
        out[bid] = inv if inv is not None else "OFFLINE"
    return jsonify(out)


@app.route("/api/requests")
def all_requests():
    out = []
    for bid, port in BANKS.items():
        reqs = safe_get(f"http://127.0.0.1:{port}/api/requests")
        if reqs:
            for r in reqs:
                r["bank_id"] = bid
                out.append(r)
    out.sort(key=lambda r: r["id"], reverse=True)
    return jsonify(out)


@app.route("/api/logs")
def all_logs():
    merged = []
    h = safe_get(f"http://127.0.0.1:{HOSPITAL_PORT}/api/logs") or []
    for row in h:
        row["node"] = "HOSPITAL"
        merged.append(row)
    for bid, port in BANKS.items():
        rows = safe_get(f"http://127.0.0.1:{port}/api/logs") or []
        for row in rows:
            row["node"] = bid
            merged.append(row)
    merged.sort(key=lambda r: (r.get("lamport") or 0, r.get("ts") or ""), reverse=True)
    return jsonify(merged[:80])


@app.route("/api/toggle/<node_id>", methods=["POST"])
def toggle(node_id):
    if node_id not in BANKS:
        return jsonify({"ok": False, "error": "unknown or non-togglable node"}), 400
    try:
        r = requests.post(f"http://127.0.0.1:{BANKS[node_id]}/api/toggle", timeout=2)
        return jsonify(r.json())
    except requests.exceptions.RequestException:
        return jsonify({"ok": False, "error": "node unreachable"}), 502


@app.route("/api/election/<node_id>", methods=["POST"])
def trigger_election(node_id):
    if node_id not in BANKS:
        return jsonify({"ok": False, "error": "unknown bank"}), 400
    try:
        r = requests.post(f"http://127.0.0.1:{BANKS[node_id]}/api/election", timeout=4)
        return jsonify(r.json())
    except requests.exceptions.RequestException:
        return jsonify({"ok": False, "error": "node unreachable"}), 502


def main():
    global HOSPITAL_PORT, BANKS
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5010)
    parser.add_argument("--hospital", type=int, default=5000)
    parser.add_argument("--banks", default="A:5001,B:5002,C:5003")
    args = parser.parse_args()

    HOSPITAL_PORT = args.hospital
    BANKS = {}
    for pair in args.banks.split(","):
        bid, bport = pair.split(":")
        BANKS[bid.upper()] = int(bport)

    print(f" * Admin dashboard starting on port {args.port}")
    app.run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
