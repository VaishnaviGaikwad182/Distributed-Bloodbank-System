"""
Blood Bank node.
Run with:  python app.py --id A --port 5001 --peers B:5002,C:5003
Each node owns its own SQLite DB (bloodbank_<ID>.db) and exposes a REST API
that the Hospital node and the Admin dashboard talk to. Designed to
demonstrate: distributed inventory, RPC-style search, message-oriented
emergency requests, mutual exclusion on reservation, node-to-node transfer,
a Lamport logical clock, a Bully-algorithm leader election, and a simple
online/offline failure toggle.
"""
import argparse
import os
import sqlite3
import threading
import time
from datetime import datetime

import requests
from flask import Flask, g, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", template_folder="templates")

BANK_ID = None
PORT = None
PEERS = {}          # {"B": 5002, "C": 5003}
DB_PATH = None
IS_ONLINE = True
LAMPORT = 0
CURRENT_COORDINATOR = None
CLOCK_LOCK = threading.Lock()
RESERVE_LOCK = threading.Lock()   # mutual exclusion guard for reservations

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]
SEED_UNITS = {"A": [10, 3, 12, 2, 15, 5, 4, 1],
              "B": [5, 2, 8, 1, 10, 0, 2, 1],
              "C": [8, 4, 5, 3, 7, 3, 1, 0]}


# ---------------------------------------------------------------- helpers --
def tick(received=None):
    """Advance the Lamport logical clock."""
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
    fresh = not os.path.exists(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS inventory(
            blood_group TEXT PRIMARY KEY,
            units INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital_name TEXT,
            blood_group TEXT,
            units INTEGER,
            req_type TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT,
            lamport INTEGER
        );
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            lamport INTEGER,
            message TEXT
        );
    """)
    if fresh:
        seed = SEED_UNITS.get(BANK_ID, [18, 7, 15, 5, 32, 11, 6, 2] if BANK_ID == "A" else [12, 4, 9, 3, 15, 2, 2, 1])
        db.executemany(
            "INSERT INTO inventory(blood_group, units) VALUES (?,?)",
            list(zip(BLOOD_GROUPS, seed)),
        )
        # Seed initial requests if empty
        cur = db.execute("SELECT COUNT(*) FROM requests")
        if cur.fetchone()[0] == 0:
            initial_reqs = [
                ("City Hospital", "O-", 3, "CRITICAL", "PENDING", "10:30:45 AM", 12),
                ("Sunrise Hospital", "A+", 2, "HIGH", "PENDING", "10:30:46 AM", 13),
                ("Life Care Hospital", "B+", 4, "NORMAL", "FULFILLED", "10:31:02 AM", 14),
                ("City Hospital", "O+", 5, "NORMAL", "PENDING", "10:31:10 AM", 15),
            ]
            db.executemany(
                "INSERT INTO requests(hospital_name, blood_group, units, req_type, status, created_at, lamport) VALUES (?,?,?,?,?,?,?)",
                initial_reqs,
            )
            # Seed initial logs
            initial_logs = [
                ("10:32:15 AM", 16, "Updated inventory for O+ (added 5 units)"),
                ("10:31:10 AM", 15, "Approved transfer request for A+ (2 units) to Blood Bank B"),
                ("10:31:02 AM", 14, "Received transfer request for A+ (2 units) from Blood Bank B"),
                ("10:30:46 AM", 13, "Sent availability response for O- (11 units) to City Hospital"),
                ("10:30:45 AM", 12, "Received blood search request for O- from City Hospital"),
            ]
            db.executemany(
                "INSERT INTO logs(ts, lamport, message) VALUES (?,?,?)",
                initial_logs,
            )
    db.commit()
    db.close()


def log_event(message, lamport=None):
    db = get_db()
    db.execute(
        "INSERT INTO logs(ts, lamport, message) VALUES (?,?,?)",
        (datetime.now().strftime("%H:%M:%S"), lamport or LAMPORT, message),
    )
    db.commit()


def offline_guard():
    if not IS_ONLINE:
        return jsonify({"error": f"Blood Bank {BANK_ID} is OFFLINE"}), 503
    return None


# -------------------------------------------------------------- frontend --
@app.route("/")
def index():
    return send_from_directory(app.template_folder, "index.html")


@app.route("/config.js")
def config_js():
    peers_json = str(dict(PEERS)).replace("'", '"')
    return (f"const BANK_ID = '{BANK_ID}'; const BANK_PORT = {PORT}; const PEERS = {peers_json};",
            200, {"Content-Type": "application/javascript"})


# ------------------------------------------------------------------ auth --
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    # Simple demo auth: username must equal bank id (case-insensitive), any password
    if data.get("username", "").strip().lower() == BANK_ID.lower():
        tick()
        log_event(f"Bank staff '{data.get('username')}' logged in")
        return jsonify({"ok": True, "bank_id": BANK_ID})
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


# --------------------------------------------------------------- status ---
@app.route("/api/status")
def status():
    return jsonify({
        "bank_id": BANK_ID,
        "port": PORT,
        "online": IS_ONLINE,
        "lamport": LAMPORT,
        "coordinator": CURRENT_COORDINATOR,
    })


@app.route("/api/toggle", methods=["POST"])
def toggle():
    global IS_ONLINE
    IS_ONLINE = not IS_ONLINE
    tick()
    log_event(f"Node manually set {'ONLINE' if IS_ONLINE else 'OFFLINE'}")
    return jsonify({"online": IS_ONLINE})


RESERVED_MAP = {"A": {"A+": 2, "A-": 1, "B+": 3, "B-": 0, "AB+": 1, "AB-": 0, "O+": 4, "O-": 2},
                "B": {"A+": 1, "A-": 0, "B+": 2, "B-": 0, "AB+": 0, "AB-": 0, "O+": 2, "O-": 0},
                "C": {"A+": 2, "A-": 1, "B+": 1, "B-": 1, "AB+": 1, "AB-": 0, "O+": 3, "O-": 1}}
EXPIRING_MAP = {"A": {"A+": 1, "A-": 0, "B+": 1, "B-": 0, "AB+": 0, "AB-": 0, "O+": 2, "O-": 2},
                "B": {"A+": 0, "A-": 0, "B+": 1, "B-": 0, "AB+": 0, "AB-": 0, "O+": 1, "O-": 0},
                "C": {"A+": 1, "A-": 0, "B+": 0, "B-": 0, "AB+": 0, "AB-": 0, "O+": 1, "O-": 0}}

# ------------------------------------------------------------- inventory --
@app.route("/api/inventory")
def inventory():
    guard = offline_guard()
    if guard:
        return guard
    db = get_db()
    rows = db.execute("SELECT blood_group, units FROM inventory ORDER BY blood_group").fetchall()
    res = []
    b_res = RESERVED_MAP.get(BANK_ID, {})
    b_exp = EXPIRING_MAP.get(BANK_ID, {})
    for r in rows:
        d = dict(r)
        d["reserved"] = b_res.get(d["blood_group"], max(0, d["units"] // 8))
        d["expiring"] = b_exp.get(d["blood_group"], max(0, d["units"] // 12))
        res.append(d)
    return jsonify(res)


@app.route("/api/inventory/add", methods=["POST"])
def inventory_add():
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    bg, units = data["blood_group"], int(data["units"])
    db = get_db()
    db.execute(
        "INSERT INTO inventory(blood_group, units) VALUES (?, ?) "
        "ON CONFLICT(blood_group) DO UPDATE SET units = units + excluded.units",
        (bg, units),
    )
    db.commit()
    tick()
    log_event(f"+{units} units {bg} added to stock")
    return jsonify({"ok": True})


@app.route("/api/inventory/update", methods=["POST"])
def inventory_update():
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    bg, units = data["blood_group"], int(data["units"])
    db = get_db()
    db.execute(
        "INSERT INTO inventory(blood_group, units) VALUES (?, ?) "
        "ON CONFLICT(blood_group) DO UPDATE SET units = excluded.units",
        (bg, units),
    )
    db.commit()
    tick()
    log_event(f"Stock for {bg} set to {units} units")
    return jsonify({"ok": True})


# --------------------------------------------------------------- search ---
@app.route("/api/search/<blood_group>")
def search(blood_group):
    """RPC-style lookup used by the Hospital node."""
    guard = offline_guard()
    if guard:
        return guard
    remote_l = request.args.get("lamport", type=int)
    tick(remote_l)
    db = get_db()
    row = db.execute("SELECT units FROM inventory WHERE blood_group=?", (blood_group,)).fetchone()
    units = row["units"] if row else 0
    return jsonify({"bank_id": BANK_ID, "blood_group": blood_group, "units": units, "lamport": LAMPORT})


# ------------------------------------------------------------- requests ---
@app.route("/api/requests", methods=["GET"])
def list_requests():
    guard = offline_guard()
    if guard:
        return guard
    db = get_db()
    rows = db.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/requests/incoming", methods=["POST"])
def incoming_request():
    """Hospital pushes an emergency/normal request to this bank."""
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    remote_l = data.get("lamport")
    l = tick(remote_l)
    db = get_db()
    cur = db.execute(
        "INSERT INTO requests(hospital_name, blood_group, units, req_type, status, created_at, lamport) "
        "VALUES (?,?,?,?,?,?,?)",
        (data.get("hospital_name", "Hospital"), data["blood_group"], int(data["units"]),
         data.get("req_type", "NORMAL"), "PENDING", datetime.now().strftime("%H:%M:%S"), l),
    )
    db.commit()
    log_event(f"Request #{cur.lastrowid} received: {data['blood_group']} x{data['units']} "
              f"({data.get('req_type', 'NORMAL')}) [L={l}]", l)
    return jsonify({"ok": True, "request_id": cur.lastrowid, "lamport": l})


@app.route("/api/requests/<int:req_id>/respond", methods=["POST"])
def respond_request(req_id):
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    decision = data["decision"]  # ACCEPT / REJECT
    db = get_db()
    req_row = db.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()
    if not req_row:
        return jsonify({"error": "not found"}), 404

    with RESERVE_LOCK:  # ---- mutual exclusion around stock check+update ----
        if decision == "ACCEPT":
            inv = db.execute("SELECT units FROM inventory WHERE blood_group=?",
                              (req_row["blood_group"],)).fetchone()
            if not inv or inv["units"] < req_row["units"]:
                db.execute("UPDATE requests SET status='REJECTED' WHERE id=?", (req_id,))
                db.commit()
                tick()
                log_event(f"Request #{req_id} auto-rejected: insufficient stock")
                return jsonify({"ok": False, "error": "Insufficient stock"}), 409
            db.execute("UPDATE inventory SET units = units - ? WHERE blood_group=?",
                       (req_row["units"], req_row["blood_group"]))
            db.execute("UPDATE requests SET status='FULFILLED' WHERE id=?", (req_id,))
        else:
            db.execute("UPDATE requests SET status='REJECTED' WHERE id=?", (req_id,))
        db.commit()
    l = tick()
    log_event(f"Request #{req_id} {decision}ED by staff [L={l}]", l)
    return jsonify({"ok": True, "status": "FULFILLED" if decision == "ACCEPT" else "REJECTED"})


# ------------------------------------------------------------- reserve ----
@app.route("/api/reserve", methods=["POST"])
def reserve():
    """
    Hospital calls this directly to reserve units instantly (emergency path).
    Wrapped in a lock + SQLite immediate transaction so two simultaneous
    hospital reservations for the same, scarce blood group cannot both
    succeed -- demonstrates a distributed mutual-exclusion critical section.
    """
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    bg, units = data["blood_group"], int(data["units"])
    hospital = data.get("hospital_name", "Hospital")
    remote_l = data.get("lamport")

    acquired = RESERVE_LOCK.acquire(timeout=5)
    if not acquired:
        return jsonify({"ok": False, "error": "Lock timeout"}), 423
    try:
        l = tick(remote_l)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT units FROM inventory WHERE blood_group=?", (bg,)).fetchone()
        available = row[0] if row else 0
        if available < units:
            conn.rollback()
            conn.close()
            log_event(f"RESERVE denied for {hospital}: {bg} x{units} (only {available} left) [L={l}]", l)
            return jsonify({"ok": False, "reserved": False, "available": available}), 409
        conn.execute("UPDATE inventory SET units = units - ? WHERE blood_group=?", (units, bg))
        conn.execute(
            "INSERT INTO requests(hospital_name, blood_group, units, req_type, status, created_at, lamport) "
            "VALUES (?,?,?,?,?,?,?)",
            (hospital, bg, units, "RESERVATION", "FULFILLED", datetime.now().strftime("%H:%M:%S"), l),
        )
        conn.commit()
        conn.close()
        log_event(f"RESERVED {units} units {bg} for {hospital} [L={l}]", l)
        return jsonify({"ok": True, "reserved": True, "bank_id": BANK_ID, "lamport": l})
    finally:
        RESERVE_LOCK.release()


# ------------------------------------------------------------- transfer ---
@app.route("/api/transfer/send", methods=["POST"])
def transfer_send():
    """This bank sends units to a peer bank (P2P communication)."""
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    target, bg, units = data["target"], data["blood_group"], int(data["units"])
    if target not in PEERS:
        return jsonify({"ok": False, "error": "Unknown peer"}), 400

    db = get_db()
    row = db.execute("SELECT units FROM inventory WHERE blood_group=?", (bg,)).fetchone()
    if not row or row["units"] < units:
        return jsonify({"ok": False, "error": "Insufficient stock to transfer"}), 409

    l = tick()
    try:
        resp = requests.post(
            f"http://127.0.0.1:{PEERS[target]}/api/transfer/receive",
            json={"blood_group": bg, "units": units, "from": BANK_ID, "lamport": l},
            timeout=3,
        )
        if resp.status_code != 200:
            log_event(f"Transfer to {target} FAILED: peer error")
            return jsonify({"ok": False, "error": "Peer rejected transfer"}), 502
    except requests.exceptions.RequestException:
        log_event(f"Transfer to {target} FAILED: peer unreachable")
        return jsonify({"ok": False, "error": f"Blood Bank {target} unreachable"}), 502

    db.execute("UPDATE inventory SET units = units - ? WHERE blood_group=?", (units, bg))
    db.commit()
    log_event(f"Transferred {units} units {bg} -> Bank {target} [L={l}]", l)
    return jsonify({"ok": True})


@app.route("/api/transfer/receive", methods=["POST"])
def transfer_receive():
    guard = offline_guard()
    if guard:
        return guard
    data = request.get_json(force=True)
    bg, units, src = data["blood_group"], int(data["units"]), data.get("from", "?")
    l = tick(data.get("lamport"))
    db = get_db()
    db.execute(
        "INSERT INTO inventory(blood_group, units) VALUES (?, ?) "
        "ON CONFLICT(blood_group) DO UPDATE SET units = units + excluded.units",
        (bg, units),
    )
    db.commit()
    log_event(f"Received {units} units {bg} <- Bank {src} [L={l}]", l)
    return jsonify({"ok": True})


# --------------------------------------------------------------- logs -----
@app.route("/api/logs")
def logs():
    db = get_db()
    rows = db.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 60").fetchall()
    return jsonify([dict(r) for r in rows])


# --------------------------------------------------- leader election ------
@app.route("/api/election", methods=["POST"])
def election():
    """Bully algorithm: contact peers with a higher id; if none respond,
    declare self coordinator and broadcast."""
    global CURRENT_COORDINATOR
    guard = offline_guard()
    if guard:
        return guard
    higher = [pid for pid in PEERS if pid > BANK_ID]
    someone_higher_alive = False
    for pid in higher:
        try:
            r = requests.get(f"http://127.0.0.1:{PEERS[pid]}/api/status", timeout=1.5)
            if r.ok and r.json().get("online"):
                someone_higher_alive = True
                requests.post(f"http://127.0.0.1:{PEERS[pid]}/api/election", timeout=1.5)
        except requests.exceptions.RequestException:
            continue

    if someone_higher_alive:
        log_event(f"Election started by {BANK_ID}: yielding to higher-ID peer")
        return jsonify({"ok": True, "coordinator": None, "note": "yielded to higher id peer"})

    # No higher node alive -> this node wins
    CURRENT_COORDINATOR = BANK_ID
    tick()
    log_event(f"Node {BANK_ID} WON election -> COORDINATOR")
    for pid, port in PEERS.items():
        try:
            requests.post(f"http://127.0.0.1:{port}/api/coordinator",
                          json={"coordinator": BANK_ID}, timeout=1.5)
        except requests.exceptions.RequestException:
            pass
    return jsonify({"ok": True, "coordinator": BANK_ID})


@app.route("/api/coordinator", methods=["POST"])
def set_coordinator():
    global CURRENT_COORDINATOR
    data = request.get_json(force=True)
    CURRENT_COORDINATOR = data["coordinator"]
    tick()
    log_event(f"Acknowledged new coordinator: Bank {CURRENT_COORDINATOR}")
    return jsonify({"ok": True})


# ------------------------------------------------------------------ main --
def main():
    global BANK_ID, PORT, PEERS, DB_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Bank id, e.g. A, B, C")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peers", default="", help="e.g. B:5002,C:5003")
    args = parser.parse_args()

    BANK_ID = args.id.upper()
    PORT = args.port
    PEERS = {}
    if args.peers:
        for pair in args.peers.split(","):
            pid, pport = pair.split(":")
            PEERS[pid.upper()] = int(pport)

    here = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(here, f"bloodbank_{BANK_ID}.db")
    init_db()

    print(f" * Blood Bank {BANK_ID} starting on port {PORT} | peers={PEERS}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
