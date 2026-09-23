"""Одноразовый SSH-хелпер деплоя: python deploy_ssh.py "команда" — пароль из docs/secrets/VM_Beget.txt"""

import re
import sys

import paramiko

HOST = "45.84.226.80"
USER = "root"


def read_password() -> str:
    text = open("docs/secrets/VM_Beget.txt", encoding="utf-8").read()
    m = re.search(r"Пароль:\s*(\S+)", text)
    if not m:
        raise SystemExit("password not found in docs/secrets/VM_Beget.txt")
    return m.group(1)


def main() -> None:
    command = sys.argv[1]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=read_password(), timeout=20, look_for_keys=False, allow_agent=False)
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
