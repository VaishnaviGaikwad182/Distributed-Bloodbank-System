# Distributed Blood Bank System

A working reference implementation of the spec: 1 Hospital node + 3 independent
Blood Bank nodes + 1 Admin dashboard, each running as a separate Flask process
with its own SQLite database and communicating over REST.

This project includes the core distributed-system features as well as the
implementation details for clocking, leader election, mutual exclusion, and
failure handling.

<img width="1895" height="867" alt="image" src="https://github.com/user-attachments/assets/c3fd8558-c6ae-41fc-8409-c12e2aa01913" />
<img width="1901" height="866" alt="image" src="https://github.com/user-attachments/assets/6b6c9050-f695-4c0b-a57e-06a608a3d187" />
<img width="1902" height="857" alt="image" src="https://github.com/user-attachments/assets/37235c63-1d38-4ea1-8a4f-f328136ccb74" />
<img width="1897" height="862" alt="image" src="https://github.com/user-attachments/assets/aff9e0f8-42a9-4091-84f0-b2f530d5e012" />
<img width="1900" height="868" alt="image" src="https://github.com/user-attachments/assets/db286a8c-2851-48be-b04f-6a87aebfc480" />
<img width="1897" height="867" alt="image" src="https://github.com/user-attachments/assets/d46f33f0-0505-4f4f-810c-c8798f7f8640" />
<img width="1896" height="868" alt="image" src="https://github.com/user-attachments/assets/b567ff4c-fd2e-4947-bfd6-ab9a6d1a1681" />
<img width="1902" height="863" alt="image" src="https://github.com/user-attachments/assets/f6b10161-81d0-4145-83bd-a34f1c67c406" />
<img width="1896" height="860" alt="image" src="https://github.com/user-attachments/assets/f1bbea2f-c767-48c3-972f-7964d842da95" />
<img width="1897" height="865" alt="image" src="https://github.com/user-attachments/assets/ee3464af-c142-436b-802f-470a31708387" />
<img width="1891" height="862" alt="image" src="https://github.com/user-attachments/assets/628fa42d-bae6-4415-a30c-db27c66f7a7c" />
<img width="1900" height="872" alt="image" src="https://github.com/user-attachments/assets/fcd28131-bff3-48ae-9a2d-93a8d7f02470" />
<img width="1896" height="871" alt="image" src="https://github.com/user-attachments/assets/d68dfc5f-7da9-4f7f-bc12-9968054862c8" />
<img width="1891" height="861" alt="image" src="https://github.com/user-attachments/assets/cde1e909-6a78-454f-b085-3e79d8e42bc0" />
<img width="1897" height="862" alt="image" src="https://github.com/user-attachments/assets/3d49e95f-765e-4793-8cc0-3742ad126d37" />


## Nodes

| Node          | Port | Database          |
|---------------|------|------------------|
| Hospital      | 5000 | `hospital/hospital.db` |
| Blood Bank A  | 5001 | `bloodbank/bloodbank_A.db` |
| Blood Bank B  | 5002 | `bloodbank/bloodbank_B.db` |
| Blood Bank C  | 5003 | `bloodbank/bloodbank_C.db` |
| Admin         | 5010 | none (pure aggregator) |

## Run it

Windows quick-start:

```bat
pip install -r requirements.txt
run_all.bat
```

Or run each service individually in separate terminals:

```bash
python bloodbank/app.py --id A --port 5001 --peers B:5002,C:5003
python bloodbank/app.py --id B --port 5002 --peers A:5001,C:5003
python bloodbank/app.py --id C --port 5003 --peers A:5001,B:5002
python hospital/app.py  --port 5000 --banks A:5001,B:5002,C:5003
python admin/app.py     --port 5010 --hospital 5000 --banks A:5001,B:5002,C:5003
```

Then open:
- Hospital portal → http://localhost:5000  (login: `hospital` / anything)
- Blood Bank A/B/C → http://localhost:5001, 5002, 5003 (login: `a`, `b`, `c` / anything)
- Admin dashboard → http://localhost:5010 (no login — control-room view)

## Feature → module map

- **Module 1 — Authentication**: simple username-based login per node (`/api/login`).
- **Module 2 — Inventory**: add/update stock per blood group (`bloodbank/app.py`).
- **Module 3 — Distributed search**: `GET /api/search/<group>` on the Hospital fans out to each bank and aggregates results.
- **Module 4 — Emergency requests**: `POST /api/emergency` / `POST /api/requests/incoming` broadcasts requests across the network; each bank accepts or rejects independently and the Hospital tracks status.
- **Module 5 — Distributed communication**:
  - RPC: hospital-to-bank `search()` and status calls.
  - Message-oriented: emergency request broadcast and status tracking.
  - P2P: `POST /api/transfer/send` allows one blood bank to transfer stock directly to another.
  - A raw socket demo is included in `socket_demo/` for classroom reference.
- **Module 6 — Distributed algorithms**:
  - **Lamport clock**: every node increments a logical clock on event generation and on message receipt; values are shown in the logs and UI.
  - **Leader election**: Bully algorithm implemented across the 3 banks. The Admin dashboard can trigger an election, and the highest active bank ID becomes coordinator.
  - **Mutual exclusion**: `POST /api/reserve` uses a thread lock and SQLite `BEGIN IMMEDIATE` to protect the stock-check-plus-decrement critical section.
  - **Failure simulation**: "Stop node" / "Simulate failure" flips a bank offline, and the hospital/admin view sees the node go down and route around it.
  - **Coordinator propagation**: once a new leader is elected, the coordinator is broadcast to peers and surfaces in the status endpoints.
  - **Distributed state tracking**: logs, request status, and bank availability are aggregated centrally by the Admin dashboard.

## Notes

- This uses Flask's built-in dev server, which is fine for a local/classroom demo but is not meant for production traffic.
- Each node is independent — if a bank process is stopped or toggled offline, the remaining nodes keep working correctly.
- The system is designed to demonstrate distributed behavior in a simple, readable way rather than as a production-grade distributed system.
