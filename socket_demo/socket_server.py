"""
Minimal raw-socket RPC demo, kept separate from the Flask nodes so you can
show the class "communication" concept without REST hiding the wire format.

Simulates a single blood bank answering a searchBlood(group) RPC over a
plain TCP socket with a JSON request/response.

Run:   python socket_server.py 9001
Then:  python socket_client.py 9001 O-
"""
import json
import socket
import sys

INVENTORY = {"A+": 10, "A-": 3, "B+": 12, "B-": 2, "O+": 15, "O-": 5, "AB+": 4, "AB-": 1}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(5)
    print(f"Socket blood-bank RPC server listening on {port}")

    while True:
        conn, addr = srv.accept()
        with conn:
            raw = conn.recv(1024).decode()
            try:
                req = json.loads(raw)
                group = req.get("blood_group")
                units = INVENTORY.get(group, 0)
                resp = {"blood_group": group, "units": units, "status": "ok"}
            except (json.JSONDecodeError, KeyError):
                resp = {"status": "error", "message": "bad request"}
            conn.sendall(json.dumps(resp).encode())
            print(f"[{addr}] request={raw.strip()} -> response={resp}")


if __name__ == "__main__":
    main()
