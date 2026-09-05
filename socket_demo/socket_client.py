"""
Client half of the raw-socket RPC demo.
Run:   python socket_client.py 9001 O-
"""
import json
import socket
import sys


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
    blood_group = sys.argv[2] if len(sys.argv) > 2 else "O-"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("127.0.0.1", port))
        s.sendall(json.dumps({"blood_group": blood_group}).encode())
        resp = json.loads(s.recv(1024).decode())
        print(f"searchBlood('{blood_group}') -> {resp}")


if __name__ == "__main__":
    main()
