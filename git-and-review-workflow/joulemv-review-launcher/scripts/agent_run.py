#!/usr/bin/env python3
"""Run the current JouleMV worktree as an isolated app for agent browser tests.

Usage: python3 /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/agent_run.py doctor|start|status|stop
The command prints JSON so a review thread can pass frontendUrl to T3 preview.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen


ROOT = Path.cwd().resolve()
KEY = hashlib.sha256(str(ROOT).encode()).hexdigest()[:14]
STATE_DIR = Path.home() / ".cache" / "joulemv-agent-run" / KEY
STATE_FILE = STATE_DIR / "state.json"
DB_PATTERN = re.compile(r"^jmv_ai_[0-9a-f]{14}$")
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class LaunchError(Exception):
    pass


def output(**values):
    print(json.dumps(values, sort_keys=True))


def run(args, *, env=None, input_text=None, log=None, timeout=60):
    with open(log, "a", encoding="utf-8") if log else open(os.devnull, "w") as sink:
        result = subprocess.run(
            args, cwd=ROOT, env=env, input=input_text, text=True,
            stdout=sink if log else subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
    if result.returncode:
        raise LaunchError("Command failed (%s); see %s" % (args[0], log or "preflight"))
    return result.stdout.strip() if result.stdout else ""


def main_checkout():
    configured = os.environ.get("T3CODE_PROJECT_ROOT")
    if configured and Path(configured).is_dir():
        return Path(configured).resolve()
    common = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
    return Path(common).resolve().parent


def load_env():
    target = ROOT / ".env"
    if not target.exists():
        source = main_checkout() / ".env"
        if not source.is_file():
            raise LaunchError("Root .env is missing here and in the main checkout")
        shutil.copy2(source, target)
        target.chmod(0o600)
    values = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = re.sub(r"^\s*export\s+", "", key).strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def source_database(values):
    for key in ("DB_URL", "DB_AUTH_URL", "DB_USER", "DB_PASS"):
        if not values.get(key):
            raise LaunchError("Missing %s in root .env" % key)
    urls = []
    for key in ("DB_URL", "DB_AUTH_URL"):
        raw = values[key]
        if not raw.startswith("jdbc:postgresql://"):
            raise LaunchError("%s must be a PostgreSQL JDBC URL" % key)
        parsed = urlparse(raw[5:])
        name = parsed.path.lstrip("/")
        if parsed.hostname not in LOOPBACK or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise LaunchError("Agent runs require a local, simply named source database")
        if "prod" in (parsed.hostname + name).lower():
            raise LaunchError("Refusing a production-looking source database")
        urls.append((parsed.hostname, parsed.port or 5432, name))
    if urls[0] != urls[1]:
        raise LaunchError("DB_URL and DB_AUTH_URL must identify the same local database")
    return urls[0]


def pg_tools():
    candidates = [Path("/Library/PostgreSQL/18/bin"), Path("/opt/homebrew/opt/postgresql@18/bin")]
    candidates.extend(Path(p).parent for p in [shutil.which("pg_dump")] if p)
    for directory in candidates:
        if all((directory / name).exists() for name in ("psql", "pg_dump", "pg_restore", "createdb", "dropdb")):
            return {name: str(directory / name) for name in ("psql", "pg_dump", "pg_restore", "createdb", "dropdb")}
    raise LaunchError("PostgreSQL 18 client tools are required")


def java_env():
    result = os.environ.copy()
    candidates = [result.get("JAVA_HOME", "")]
    if shutil.which("brew"):
        prefix = subprocess.run(["brew", "--prefix", "openjdk@25"], capture_output=True, text=True)
        if prefix.returncode == 0:
            candidates.append(str(Path(prefix.stdout.strip()) / "libexec/openjdk.jdk/Contents/Home"))
    for home in candidates:
        java = Path(home) / "bin/java" if home else Path(shutil.which("java") or "")
        if java.is_file():
            version = subprocess.run([str(java), "-version"], capture_output=True, text=True)
            if re.search(r'version "25(?:\.|\")', version.stderr):
                if home:
                    result["JAVA_HOME"] = home
                    result["PATH"] = str(Path(home) / "bin") + os.pathsep + result.get("PATH", "")
                return result
    raise LaunchError("Java 25 is required (install openjdk@25 or set JAVA_HOME)")


def preflight():
    if not (ROOT / "AGENT.md").is_file() or not (ROOT / "pom.xml").is_file() or not (ROOT / "frontend/package-lock.json").is_file():
        raise LaunchError("Run this command from a JouleMV worktree root")
    for name in ("git", "mvn", "npm"):
        if not shutil.which(name):
            raise LaunchError("Missing required command: %s" % name)
    env = load_env()
    host, port, name = source_database(env)
    tools = pg_tools()
    client_version = run([tools["pg_dump"], "--version"])
    if not re.search(r"\b18(?:\.|\b)", client_version):
        raise LaunchError("PostgreSQL 18 pg_dump is required")
    java = java_env()
    db_env = {**os.environ, "PGPASSWORD": env["DB_PASS"]}
    query = "SELECT current_setting('server_version_num'), pg_database_size(current_database()), (SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname=current_user)"
    raw = run([tools["psql"], "-X", "-A", "-t", "-F", "|", "-h", host, "-p", str(port),
               "-U", env["DB_USER"], "-d", name, "-c", query], env=db_env, timeout=15)
    try:
        version, size, can_create = raw.split("|")
        if int(version) // 10000 != 18 or can_create != "t":
            raise ValueError()
    except ValueError:
        raise LaunchError("Source must be PostgreSQL 18 and DB_USER must be able to create databases")
    return env, (host, port, name), tools, java, round(int(size) / 1048576, 1)


def prepare_frontend():
    frontend = ROOT / "frontend"
    lock = frontend / "package-lock.json"
    modules = frontend / "node_modules"
    if not lock.is_file():
        raise LaunchError("frontend/package-lock.json is missing")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if modules.is_symlink():
        main = main_checkout() / "frontend"
        if not modules.exists() or not (main / "package-lock.json").exists() or digest != hashlib.sha256((main / "package-lock.json").read_bytes()).hexdigest():
            modules.unlink()
    marker = modules / ".jmv-agent-lock-sha256"
    if modules.is_dir() and not modules.is_symlink() and marker.is_file() and marker.read_text().strip() == digest:
        return "installed"
    if modules.is_symlink():
        return "linked"
    if not modules.exists():
        main = main_checkout() / "frontend"
        main_modules = main / "node_modules"
        main_lock = main / "package-lock.json"
        if main_modules.is_dir() and main_lock.is_file() and digest == hashlib.sha256(main_lock.read_bytes()).hexdigest():
            modules.symlink_to(main_modules, target_is_directory=True)
            return "linked"
    run(["npm", "--prefix", "frontend", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        log=STATE_DIR / "npm.log", timeout=600)
    marker.write_text(digest)
    return "installed"


def jdbc_url(host, port, name):
    return "jdbc:postgresql://%s:%s/%s" % (host, port, name)


def database_exists(tools, db_env, host, port, user, name):
    raw = run([tools["psql"], "-X", "-A", "-t", "-h", host, "-p", str(port), "-U", user,
               "-d", "postgres", "-c", "SELECT 1 FROM pg_database WHERE datname = '%s'" % name], env=db_env)
    return raw == "1"


def drop_agent_db(state, values, source, tools):
    if not state.get("dbCreated"):
        return
    name = state.get("dbName", "")
    if not DB_PATTERN.fullmatch(name) or state.get("repo") != str(ROOT) or name == source[2]:
        raise LaunchError("Refusing to drop a database not owned by this worktree")
    db_env = {**os.environ, "PGPASSWORD": values["DB_PASS"]}
    run([tools["dropdb"], "--if-exists", "--force", "-h", source[0], "-p", str(source[1]),
         "-U", values["DB_USER"], name], env=db_env, log=STATE_DIR / "database.log", timeout=60)


def create_agent_db(state, values, source, tools):
    host, port, source_name = source
    name = state["dbName"]
    db_env = {**os.environ, "PGPASSWORD": values["DB_PASS"]}
    if database_exists(tools, db_env, host, port, values["DB_USER"], name):
        raise LaunchError("Refusing to reuse an existing database without a ready agent state")
    run([tools["createdb"], "-h", host, "-p", str(port), "-U", values["DB_USER"], name],
        env=db_env, log=STATE_DIR / "database.log", timeout=60)
    state["dbCreated"] = True
    write_state(state)
    archive = STATE_DIR / "seed.dump"
    try:
        run([tools["pg_dump"], "-Fc", "--no-owner", "--no-privileges", "-h", host,
             "-p", str(port), "-U", values["DB_USER"], "-d", source_name, "-f", str(archive)],
            env=db_env, log=STATE_DIR / "database.log", timeout=600)
        run([tools["pg_restore"], "--exit-on-error", "--no-owner", "--no-privileges", "-h", host,
             "-p", str(port), "-U", values["DB_USER"], "-d", name, str(archive)],
            env=db_env, log=STATE_DIR / "database.log", timeout=600)
    finally:
        archive.unlink(missing_ok=True)
    sql_path = ROOT / "src/main/sql/update-tenant.sql"
    sql = sql_path.read_text(encoding="utf-8")
    if not re.match(r"^SET search_path TO [A-Za-z_][A-Za-z0-9_]*;\s*\n", sql):
        raise LaunchError("Unexpected update-tenant.sql header; migration was not run")
    tenant_query = ("SELECT t.code FROM root.tenant t "
                    "JOIN pg_namespace n ON n.nspname = t.code "
                    "JOIN pg_class c ON c.relnamespace = n.oid "
                    "AND c.relname = 'database_version_history' AND c.relkind IN ('r', 'p') "
                    "ORDER BY t.code")
    tenants = run([tools["psql"], "-X", "-A", "-t", "-h", host, "-p", str(port),
                   "-U", values["DB_USER"], "-d", name, "-c", tenant_query],
                  env=db_env).splitlines()
    if not tenants:
        raise LaunchError("No tenant schemas found in isolated database")
    for tenant in tenants:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tenant):
            raise LaunchError("Invalid tenant schema in isolated database")
        tenant_sql = re.sub(r"^SET search_path TO [A-Za-z_][A-Za-z0-9_]*;", "SET search_path TO " + tenant + ";", sql, count=1)
        run([tools["psql"], "-X", "-v", "ON_ERROR_STOP=1", "-h", host, "-p", str(port),
             "-U", values["DB_USER"], "-d", name], env=db_env, input_text=tenant_sql,
            log=STATE_DIR / "database.log", timeout=300)
    return len(tenants)


def port_free(port):
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(base, override):
    if override:
        port = int(override)
        if not 1024 <= port <= 65535 or not port_free(port):
            raise LaunchError("Requested port %s is unavailable" % port)
        return port
    for port in range(base, base + 100):
        if port_free(port):
            return port
    raise LaunchError("No free port near %s" % base)


def http_ready(url):
    try:
        with urlopen(url, timeout=2) as response:
            return 200 <= response.status < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def process_stamp(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def alive(pid, stamp=None):
    try:
        os.kill(pid, 0)
        return not stamp or process_stamp(pid) == stamp
    except (OSError, TypeError):
        return False


def read_state():
    if not STATE_FILE.exists():
        return None
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if data.get("repo") != str(ROOT) or data.get("owner") != "joulemv-agent-run":
        raise LaunchError("State file does not belong to this worktree")
    return data


def write_state(data):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(STATE_FILE)


def stop_process(pid, stamp=None):
    if not pid or not alive(pid, stamp):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        if not alive(pid, stamp):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def lock_state():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(STATE_DIR / ".lock", "a+")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def start(timeout):
    with lock_state():
        existing = read_state()
        if existing:
            if existing.get("commit") != run(["git", "rev-parse", "HEAD"]):
                raise LaunchError("Worktree HEAD changed; stop this run before starting the new revision")
            if alive(existing.get("backendPid"), existing.get("backendStamp")) and \
                    alive(existing.get("frontendPid"), existing.get("frontendStamp")) and \
                    http_ready(existing["backendUrl"] + "/login") and http_ready(existing["frontendUrl"]):
                output(ok=True, status="already_ready", **{k: existing[k] for k in ("frontendUrl", "backendUrl", "commit", "dbName")})
                return
            raise LaunchError("Previous agent run is incomplete; inspect status and run stop before retrying")
        values, source, tools, java, size = preflight()
        modules = prepare_frontend()
        offset = int(KEY[:8], 16) % 40
        backend_port = choose_port(8100 + offset, os.environ.get("JMV_BACKEND_PORT"))
        debug_port = choose_port(8300 + offset, os.environ.get("JMV_DEBUG_PORT"))
        frontend_port = choose_port(5200 + offset, os.environ.get("JMV_FRONTEND_PORT"))
        backend_url = "http://127.0.0.1:%s" % backend_port
        frontend_url = "http://127.0.0.1:%s" % frontend_port
        commit = run(["git", "rev-parse", "HEAD"])
        state = {"owner": "joulemv-agent-run", "repo": str(ROOT), "commit": commit,
                 "dbName": "jmv_ai_" + KEY, "backendUrl": backend_url,
                 "frontendUrl": frontend_url, "backendPid": None, "frontendPid": None,
                 "dbCreated": False, "phase": "cloning", "createdAt": time.time()}
        write_state(state)
        try:
            tenant_count = create_agent_db(state, values, source, tools)
            app_env = {**java, **values,
                       "DB_URL": jdbc_url(source[0], source[1], state["dbName"]),
                       "DB_AUTH_URL": jdbc_url(source[0], source[1], state["dbName"]),
                       "SERVER_PORT": str(backend_port),
                       "FRONTEND_URL": frontend_url,
                       "ALLOWED_ORIGINS": ",".join(filter(None, [values.get("ALLOWED_ORIGINS"), frontend_url])),
                       "APP_LOCAL_DIR": str(STATE_DIR / "files"),
                       "SENTRY_DSN": "", "SENTRY_LOGS_ENABLED": "false",
                       "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "1",
                       "FORMULA_THRESHOLD_NOTIFICATION_POLL_DELAY_MS": "86400000"}
            (STATE_DIR / "files").mkdir(exist_ok=True)
            frontend_env = {**os.environ, **values, "VITE_API_URL": backend_url,
                            "VITE_LEGACY_API_URL": backend_url, "VITE_SENTRY_DSN": ""}
            with open(STATE_DIR / "backend.log", "a", encoding="utf-8") as be_log:
                backend = subprocess.Popen(["mvn", "-q", "spring-boot:run", "-Djmv.debug.port=%s" % debug_port],
                                           cwd=ROOT, env=app_env, stdout=be_log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            state["backendPid"] = backend.pid
            state["backendStamp"] = process_stamp(backend.pid)
            state["phase"] = "starting"
            write_state(state)
            with open(STATE_DIR / "frontend.log", "a", encoding="utf-8") as fe_log:
                frontend = subprocess.Popen(["npm", "--prefix", "frontend", "run", "dev", "--",
                                             "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"],
                                            cwd=ROOT, env=frontend_env, stdout=fe_log,
                                            stderr=subprocess.STDOUT, start_new_session=True)
            state["frontendPid"] = frontend.pid
            state["frontendStamp"] = process_stamp(frontend.pid)
            write_state(state)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if backend.poll() is not None or frontend.poll() is not None:
                    raise LaunchError("A server exited during startup; see backend.log and frontend.log")
                if http_ready(backend_url + "/login") and http_ready(frontend_url):
                    state["phase"] = "ready"
                    write_state(state)
                    output(ok=True, status="ready", frontendUrl=frontend_url,
                           backendUrl=backend_url, commit=commit, dbIsolation="clone",
                           dbName=state["dbName"], sourceSizeMiB=size,
                           migratedTenants=tenant_count, frontendDependencies=modules,
                           logs=str(STATE_DIR))
                    return
                time.sleep(2)
            raise LaunchError("Timed out waiting for backend /login and frontend /; see logs in %s" % STATE_DIR)
        except Exception as startup_error:
            stop_process(state.get("backendPid"), state.get("backendStamp"))
            stop_process(state.get("frontendPid"), state.get("frontendStamp"))
            try:
                drop_agent_db(state, values, source, tools)
            except Exception as cleanup_error:
                raise LaunchError("Startup failed: %s; database cleanup failed: %s; state retained for retry" %
                                  (startup_error, cleanup_error)) from cleanup_error
            STATE_FILE.unlink(missing_ok=True)
            raise


def status():
    state = read_state()
    if not state:
        output(ok=False, status="stopped", worktree=str(ROOT))
        return 1
    backend = alive(state.get("backendPid"), state.get("backendStamp")) and http_ready(state["backendUrl"] + "/login")
    frontend = alive(state.get("frontendPid"), state.get("frontendStamp")) and http_ready(state["frontendUrl"])
    ready = backend and frontend
    output(ok=ready, status="ready" if ready else "unhealthy",
           backendReady=backend, frontendReady=frontend,
           frontendUrl=state["frontendUrl"], backendUrl=state["backendUrl"],
           commit=state["commit"], logs=str(STATE_DIR))
    return 0 if ready else 1


def stop():
    with lock_state():
        state = read_state()
        if not state:
            output(ok=True, status="already_stopped")
            return
        stop_process(state.get("backendPid"), state.get("backendStamp"))
        stop_process(state.get("frontendPid"), state.get("frontendStamp"))
        values = load_env()
        source = source_database(values)
        tools = pg_tools()
        drop_agent_db(state, values, source, tools)
        STATE_FILE.unlink(missing_ok=True)
        output(ok=True, status="stopped", databaseDropped=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    start_parser = sub.add_parser("start")
    start_parser.add_argument("--timeout", type=int, default=600)
    sub.add_parser("status")
    sub.add_parser("stop")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            _, _, _, _, size = preflight()
            output(ok=True, status="ready_to_start", worktree=str(ROOT), sourceSizeMiB=size)
        elif args.command == "start":
            if args.timeout <= 0:
                raise LaunchError("--timeout must be positive")
            start(args.timeout)
        elif args.command == "status":
            return status()
        else:
            stop()
    except (LaunchError, OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as exc:
        output(ok=False, status="error", message=str(exc), logs=str(STATE_DIR))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
