# Distributed Blood Bank Availability System

A working reference implementation of the spec: 1 Hospital node + 3 independent
Blood Bank nodes + 1 Admin dashboard, each a separate Flask process with its
own SQLite database, talking to each other over REST.

## Nodes

| Node          | Port | Database          |
|---------------|------|--------------------|
| Hospital      | 5000 | `hospital/hospital.db` |
| Blood Bank A  | 5001 | `bloodbank/bloodbank_A.db` |
| Blood Bank B  | 5002 | `bloodbank/bloodbank_B.db` |
| Blood Bank C  | 5003 | `bloodbank/bloodbank_C.db` |
| Admin         | 5010 | none (pure aggregator) |

## Run it

```bash
pip install -r requirements.txt
./run_all.sh
```

Then open:
- Hospital terminal → http://localhost:5000  (login: `hospital` / anything)
- Blood Bank A/B/C  → http://localhost:5001, 5002, 5003 (login: `a`, `b`, `c` / anything)
- Admin dashboard   → http://localhost:5010 (no login — control-room view)

On Windows, or to run nodes individually, skip `run_all.sh` and start each in
its own terminal:

```bash
python bloodbank/app.py --id A --port 5001 --peers B:5002,C:5003
python bloodbank/app.py --id B --port 5002 --peers A:5001,C:5003
python bloodbank/app.py --id C --port 5003 --peers A:5001,B:5002
python hospital/app.py  --port 5000 --banks A:5001,B:5002,C:5003
python admin/app.py     --port 5010 --hospital 5000 --banks A:5001,B:5002,C:5003
```

## Feature → module map

- **Module 1 — Authentication**: simple username-based login per node (`/api/login`).
- **Module 2 — Inventory**: add/update stock per blood group (`bloodbank/app.py`).
- **Module 3 — Distributed search**: `GET /api/search/<group>` on the Hospital
  fans out to every bank's own `/api/search/<group>` (RPC-style) and aggregates.
- **Module 4 — Emergency requests**: `POST /api/emergency` broadcasts to all
  banks (message-oriented comms); each bank Accepts/Rejects independently;
  Hospital polls `GET /api/track` for live status.
- **Module 5 — Distributed communication**:
  - RPC: `search()` call/response between Hospital and each bank.
  - Message-oriented: the emergency-request broadcast.
  - P2P: `POST /api/transfer/send` lets one bank ship stock straight to another.
  - (A minimal raw-socket RPC demo for the write-up is in `socket_demo/`.)
- **Module 6 — Distributed algorithms (FA-2)**:
  - **Lamport clock**: every node keeps a logical clock, advanced on every
    send/receive and shown in the log panel and topbar.
  - **Leader election**: Bully algorithm across the 3 banks — trigger from
    the Admin dashboard ("Trigger election"); the highest-ID online bank wins.
  - **Mutual exclusion**: `POST /api/reserve` wraps the stock check + decrement
    in a thread lock + `BEGIN IMMEDIATE` SQLite transaction, so two
    simultaneous reservations for the last unit can't both succeed.
  - **Failure simulation**: "Stop node" / "Simulate failure" toggles an
    online flag on a bank; the Hospital and Admin dashboard immediately see
    it drop offline and route around it.
  - Vector clocks aren't wired in by default (Lamport is), but the same
    `tick()` pattern in `app.py` is the place to extend it if your syllabus
    wants both.

## Notes

- This uses Flask's built-in dev server, which is fine for a local/classroom
  demo but isn't meant for production traffic.
- Each node is fully independent — kill any Blood Bank process (or use
  the "Stop node" button) and the rest of the system keeps working, which is
  the point of the demo.
