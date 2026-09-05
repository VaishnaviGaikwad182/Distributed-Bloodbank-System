"""
Hospital node.
Run with: python app.py --port 5000 --banks A:5001,B:5002,C:5003
Talks to every Blood Bank node over REST (playing the role of RPC /
message-oriented communication for the syllabus write-up), aggregates
availability, broadcasts emergency requests, and reserves stock.
"""
import argparse
import os
import sqlite3
import threading
from datetime import datetime

import requests
from flask import Flask, g, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

PORT = None
BANKS = {}  # {"A": 5001, "B": 5002, "C": 5003}
DB_PATH = None
LAMPORT = 0
CLOCK_LOCK = threading.Lock()


def tick(received=None):
    global LAMPORT
    with CLOCK_LOCK:
        if received is not None:
            LAMPORT = max(LAMPORT, received) + 1
        else:
            LAMPORT += 1
        return LAMPORT


def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(_exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tracked_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_id TEXT,
            bank_request_id INTEGER,
            blood_group TEXT,
            units INTEGER,
            req_type TEXT,
            priority TEXT DEFAULT 'NORMAL',
            status TEXT,
            created_at TEXT,
            lamport INTEGER
        );
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            direction TEXT DEFAULT 'LOG',
            lamport INTEGER,
            message TEXT
        );
    """)
    tracked_columns = {row[1] for row in db.execute("PRAGMA table_info(tracked_requests)")}
    if "priority" not in tracked_columns:
        db.execute("ALTER TABLE tracked_requests ADD COLUMN priority TEXT DEFAULT 'NORMAL'")

    log_columns = {row[1] for row in db.execute("PRAGMA table_info(logs)")}
    if "direction" not in log_columns:
        db.execute("ALTER TABLE logs ADD COLUMN direction TEXT DEFAULT 'LOG'")

    cur = db.execute("SELECT COUNT(*) FROM tracked_requests")
    if cur.fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO tracked_requests(id, bank_id, bank_request_id, blood_group, units, req_type, priority, status, created_at, lamport) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (1023, "C", 3, "O-", 5, "EMERGENCY", "CRITICAL", "RESERVED", "20 May 2024 10:15 AM", 15),
                (1022, "A", 1, "A+", 2, "HIGH", "HIGH", "PENDING", "20 May 2024 09:40 AM", 14),
                (1021, "B", 2, "B+", 4, "NORMAL", "NORMAL", "SEARCHING", "19 May 2024 06:30 PM", 13),
            ]
        )
        db.executemany(
            "INSERT INTO logs(ts, direction, lamport, message) VALUES (?,?,?,?)",
            [
                ("10:16:07 AM", "RECEIVED", 15, "Availability response for O- from Blood Bank C (5 units)"),
                ("10:16:06 AM", "RECEIVED", 14, "Availability response for O- from Blood Bank B (0 units)"),
                ("10:16:05 AM", "RECEIVED", 13, "Availability response for O- from Blood Bank A (2 units)"),
                ("10:16:10 AM", "SENT", 12, "Blood search request for O- (5 units) to all nodes"),
                ("10:15:20 AM", "RECEIVED", 11, "Request REQ1023 accepted by Blood Bank C"),
            ]
        )
    db.commit()
    db.close()


def log_event(message, lamport=None, direction="LOG"):
    db = get_db()
    db.execute("INSERT INTO logs(ts, direction, lamport, message) VALUES (?,?,?,?)",
               (datetime.now().strftime("%H:%M:%S"), direction, lamport or LAMPORT, message))
    db.commit()


# -------------------------------------------------------------- frontend --
@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/config.js")
def config_js():
    return f"const BANKS = {dict(BANKS)!r};".replace("'", '"'), 200, {"Content-Type": "application/javascript"}


# ------------------------------------------------------------------ auth --
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    if data.get("username", "").strip().lower() == "hospital":
        tick()
        log_event(f"Hospital staff '{data.get('username')}' logged in")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


# ---------------------------------------------------------------- status --
@app.route("/api/banks/status")
def banks_status():
    out = []
    for bid, port in BANKS.items():
        try:
            r = requests.get(f"http://127.0.0.1:{port}/api/status", timeout=1.5)
            d = r.json()
            d["reachable"] = True
        except requests.exceptions.RequestException:
            d = {"bank_id": bid, "port": port, "online": False, "reachable": False}
        out.append(d)
    return jsonify(out)


# ------------------------------------------------- inventory matrix ----
@app.route("/api/inventory/matrix")
def inventory_matrix():
    groups = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
    bank_meta = {
        "A": {"name": "Blood Bank A", "dist": "2.1 km", "loc": "Pune", "time": "20 May 2024 10:16 AM"},
        "B": {"name": "Blood Bank B", "dist": "5.4 km", "loc": "Mumbai", "time": "20 May 2024 10:15 AM"},
        "C": {"name": "Blood Bank C", "dist": "8.7 km", "loc": "Nagpur", "time": "20 May 2024 10:14 AM"},
    }
    rows = []
    totals = {g: 0 for g in groups}
    
    for bid, port in BANKS.items():
        meta = bank_meta.get(bid, {"name": f"Blood Bank {bid}", "dist": "5.0 km", "time": datetime.now().strftime("%d %b %Y %I:%M %p")})
        item = {
            "bank_id": bid,
            "node_name": f"{meta['name']} ({meta['dist']})",
            "last_updated": meta["time"],
            "online": True,
            "counts": {}
        }
        try:
            r = requests.get(f"http://127.0.0.1:{port}/api/inventory", timeout=1.5)
            if r.status_code == 200:
                inv_data = r.json()
                for row in inv_data:
                    bg = row["blood_group"]
                    u = row["units"]
                    item["counts"][bg] = u
                    if bg in totals:
                        totals[bg] += u
            else:
                item["online"] = False
        except requests.exceptions.RequestException:
            item["online"] = False
        
        # Fill missing groups with 0
        for g in groups:
            if g not in item["counts"]:
                item["counts"][g] = 0
                
        rows.append(item)
        
    return jsonify({"rows": rows, "totals": totals, "groups": groups})


# ---------------------------------------------------------------- search --
@app.route("/api/search/<blood_group>")
def search(blood_group):
    l = tick()
    results = []
    for bid, port in BANKS.items():
        try:
            r = requests.get(f"http://127.0.0.1:{port}/api/search/{blood_group}",
                              params={"lamport": l}, timeout=2)
            if r.status_code == 200:
                data = r.json()
                tick(data.get("lamport"))
                results.append({"bank_id": bid, "units": data["units"], "online": True})
            else:
                results.append({"bank_id": bid, "units": 0, "online": False})
        except requests.exceptions.RequestException:
            results.append({"bank_id": bid, "units": 0, "online": False})
    total = sum(r["units"] for r in results)
    log_event(f"Searched {blood_group} across {len(BANKS)} banks -> total {total} units [L={LAMPORT}]")
    return jsonify({"blood_group": blood_group, "results": results, "total": total})


# ------------------------------------------------------------- reserve ----
@app.route("/api/reserve", methods=["POST"])
def reserve():
    data = request.get_json(force=True)
    bank_id, bg, units = data["bank_id"], data["blood_group"], int(data["units"])
    if bank_id not in BANKS:
        return jsonify({"ok": False, "error": "Unknown bank"}), 400
    l = tick()
    try:
        r = requests.post(
            f"http://127.0.0.1:{BANKS[bank_id]}/api/reserve",
            json={"blood_group": bg, "units": units, "hospital_name": "Hospital", "lamport": l},
            timeout=4,
        )
        result = r.json()
    except requests.exceptions.RequestException:
        log_event(f"Reserve FAILED: Bank {bank_id} unreachable")
        return jsonify({"ok": False, "error": f"Blood Bank {bank_id} unreachable"}), 502

    db = get_db()
    status = "FULFILLED" if result.get("reserved") else "REJECTED"
    db.execute(
        "INSERT INTO tracked_requests(bank_id, bank_request_id, blood_group, units, req_type, status, created_at, lamport) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (bank_id, None, bg, units, "RESERVATION", status, datetime.now().strftime("%H:%M:%S"), l),
    )
    db.commit()
    log_event(f"Reservation {bg} x{units} @ Bank {bank_id}: {status} [L={l}]")
    return jsonify(result)


# ------------------------------------------------------------ emergency ---
@app.route("/api/emergency", methods=["POST"])
def emergency():
    """Broadcast a request to every reachable bank (message-oriented comms)."""
    data = request.get_json(force=True)
    bg, units = data["blood_group"], int(data["units"])
    req_type = data.get("req_type", "EMERGENCY")
    l = tick()
    db = get_db()
    sent_to = []
    for bid, port in BANKS.items():
        try:
            r = requests.post(
                f"http://127.0.0.1:{port}/api/requests/incoming",
                json={"hospital_name": "Hospital", "blood_group": bg, "units": units,
                      "req_type": req_type, "lamport": l},
                timeout=2,
            )
            if r.status_code == 200:
                rd = r.json()
                tick(rd.get("lamport"))
                db.execute(
                    "INSERT INTO tracked_requests(bank_id, bank_request_id, blood_group, units, req_type, "
                    "status, created_at, lamport) VALUES (?,?,?,?,?,?,?,?)",
                    (bid, rd["request_id"], bg, units, req_type, "PENDING",
                     datetime.now().strftime("%H:%M:%S"), l),
                )
                sent_to.append(bid)
        except requests.exceptions.RequestException:
            continue
    db.commit()
    log_event(f"EMERGENCY broadcast {bg} x{units} -> banks {sent_to} [L={l}]")
    return jsonify({"ok": True, "sent_to": sent_to})


@app.route("/api/track")
def track():
    """Refresh status of every tracked request by polling its origin bank."""
    db = get_db()
    rows = db.execute("SELECT * FROM tracked_requests ORDER BY id DESC").fetchall()
    out = []
    for row in rows:
        row = dict(row)
        if row["bank_request_id"] is not None and row["status"] == "PENDING":
            try:
                r = requests.get(f"http://127.0.0.1:{BANKS[row['bank_id']]}/api/requests", timeout=1.5)
                if r.status_code == 200:
                    for br in r.json():
                        if br["id"] == row["bank_request_id"] and br["status"] != row["status"]:
                            db.execute("UPDATE tracked_requests SET status=? WHERE id=?",
                                       (br["status"], row["id"]))
                            db.commit()
                            row["status"] = br["status"]
            except requests.exceptions.RequestException:
                pass
        out.append(row)
    return jsonify(out)


@app.route("/api/logs")
def logs():
    db = get_db()
    rows = db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 60").fetchall()
    return jsonify([dict(r) for r in rows])


def main():
    global PORT, BANKS, DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--banks", default="A:5001,B:5002,C:5003")
    args = parser.parse_args()

    PORT = args.port
    BANKS = {}
    for pair in args.banks.split(","):
        bid, bport = pair.split(":")
        BANKS[bid.upper()] = int(bport)

    here = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(here, "hospital.db")
    init_db()

    print(f" * Hospital starting on port {PORT} | banks={BANKS}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
