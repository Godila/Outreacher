"""SSH-хелпер деплоя: python deploy_ssh.py <host|all> "команда" — пароли из docs/secrets/VM_*.txt"""

import re
import sys
from pathlib import Path

import paramiko

USER = "root"


def read_password(host: str) -> str:
    for path in Path("docs/secrets").glob("VM_*.txt"):
        text = path.read_text(encoding="utf-8")
        if host in text:
            # берём пароль из блока, где упоминается этот IP
            block = text.split(host, 1)[1][:200]
            m = re.search(r"Пароль:\s*(\S+)", block)
            if m:
                return m.group(1)
    raise SystemExit(f"password for {host} not found in docs/secrets/VM_*.txt")


def main() -> None:
    host = sys.argv[1]
    command = sys.argv[2]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=USER, password=read_password(host), timeout=20, look_for_keys=False, allow_agent=False)
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=600)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if out:
            print(out, end="")
        if err:
            print("STDERR:", err, end="", file=sys.stderr)
        sys.exit(code)
    finally:
        client.close()


if __name__ == "__main__":
    main()
