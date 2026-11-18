"""Start a localhost-only, password-protected Postgres for optional tests.

Never binds 0.0.0.0. The URL file is mode 600 under /tmp and is not committed.
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
from pathlib import Path

HARNESS_DIR = Path(os.environ.get("OIRO_TEST_PGDATA_ROOT", "/tmp/oiro-pgtest"))
PGDATA = HARNESS_DIR / "data"
PORT = int(os.environ.get("OIRO_TEST_PGPORT", "55432"))
USER = "oiro"
DBNAME = "oiro_test"
PW_FILE = HARNESS_DIR / "password"
URL_FILE = HARNESS_DIR / "url"


def _bin_dir() -> Path | None:
    env = os.environ.get("OIRO_POSTGRES_BIN")
    if env and Path(env).is_dir():
        return Path(env)
    try:
        import embedded_postgres.postgres_server as server

        path = Path(server.POSTGRES_BIN_PATH)
        if (path / "initdb").is_file():
            return path
    except Exception:
        pass
    for candidate in (Path("/usr/lib/postgresql/17/bin"), Path("/usr/lib/postgresql/16/bin"), Path("/usr/bin")):
        if (candidate / "initdb").is_file():
            return candidate
    return None


def postgres_bins_available() -> bool:
    return _bin_dir() is not None


def _cmd(name: str) -> str:
    bins = _bin_dir()
    if bins is None:
        raise RuntimeError("未找到本地 PostgreSQL 可执行文件")
    return str(bins / name)


def _password() -> str:
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    if PW_FILE.is_file():
        return PW_FILE.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(18)
    fd = os.open(PW_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(password + "\n")
    return password


def _write_url(password: str) -> str:
    url = f"postgresql+psycopg://{USER}:{password}@127.0.0.1:{PORT}/{DBNAME}"
    fd = os.open(URL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(url + "\n")
    return url


def _running() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.3):
            return True
    except OSError:
        return False


def connection_url() -> str | None:
    env = (os.environ.get("OIRO_TEST_DATABASE_URL") or "").strip()
    if env:
        return env
    if URL_FILE.is_file() and _running():
        return URL_FILE.read_text(encoding="utf-8").strip()
    return None


def start() -> str:
    existing = connection_url()
    if existing:
        return existing
    bins = _bin_dir()
    if bins is None:
        raise RuntimeError("未找到 PostgreSQL 可执行文件，无法启动测试库")
    password = _password()
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = str(bins) + os.pathsep + env.get("PATH", "")
    env["LC_ALL"] = env.get("LC_ALL") or "C"

    if not (PGDATA / "PG_VERSION").exists():
        if PGDATA.exists():
            shutil.rmtree(PGDATA)
        PGDATA.mkdir(parents=True, exist_ok=True)
        pw_tmp = HARNESS_DIR / "pwfile"
        pw_tmp.write_text(password + "\n", encoding="utf-8")
        os.chmod(pw_tmp, 0o600)
        subprocess.run(
            [
                _cmd("initdb"),
                "-D",
                str(PGDATA),
                "-U",
                USER,
                "--auth-local=scram-sha-256",
                "--auth-host=scram-sha-256",
                "--pwfile",
                str(pw_tmp),
                "--encoding=UTF8",
                "--locale=C",
            ],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        conf = PGDATA / "postgresql.conf"
        extra = (
            "\nlisten_addresses = '127.0.0.1'\n"
            f"port = {PORT}\n"
            "unix_socket_directories = '{0}'\n".format(HARNESS_DIR)
        )
        conf.write_text(conf.read_text(encoding="utf-8") + extra, encoding="utf-8")
        (PGDATA / "pg_hba.conf").write_text(
            "local all all scram-sha-256\n"
            "host all all 127.0.0.1/32 scram-sha-256\n"
            "host all all ::1/128 scram-sha-256\n",
            encoding="utf-8",
        )

    if not _running():
        subprocess.run(
            [_cmd("pg_ctl"), "-D", str(PGDATA), "-l", str(HARNESS_DIR / "postgres.log"), "-w", "start"],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
    # Wait until TCP is up.
    for _ in range(40):
        if _running():
            break
        time.sleep(0.15)
    if not _running():
        raise RuntimeError("测试 PostgreSQL 未能在 127.0.0.1 上监听")

    url = _write_url(password)
    env["PGPASSWORD"] = password
    exists = subprocess.run(
        [_cmd("psql"), "-h", "127.0.0.1", "-p", str(PORT), "-U", USER, "-d", "postgres", "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname='{DBNAME}'"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    if exists.stdout.strip() != "1":
        subprocess.run(
            [_cmd("createdb"), "-h", "127.0.0.1", "-p", str(PORT), "-U", USER, DBNAME],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
    return url


def stop() -> None:
    bins = _bin_dir()
    if bins is None or not (PGDATA / "PG_VERSION").exists():
        return
    env = os.environ.copy()
    env["PATH"] = str(bins) + os.pathsep + env.get("PATH", "")
    subprocess.run(
        [_cmd("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "stop"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
