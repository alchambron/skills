#!/usr/bin/env python3
"""Focused runtime safety tests; no database or application servers are started."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError


SCRIPT = Path(__file__).with_name("agent_run.py")
spec = importlib.util.spec_from_file_location("review_all_agent_run", SCRIPT)
agent_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_run)


class Response:
    def __init__(self, body=b"", content_type="text/javascript", status=200):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        (self.root / "frontend").mkdir()
        self.root_patch = patch.object(agent_run, "ROOT", self.root)
        self.dir_patch = patch.object(agent_run, "STATE_DIR", self.state_dir)
        self.file_patch = patch.object(agent_run, "STATE_FILE", self.state_dir / "state.json")
        for item in (self.root_patch, self.dir_patch, self.file_patch):
            item.start()
            self.addCleanup(item.stop)
        self.values = {"DB_USER": "localuser", "DB_PASS": "private-test-value",
                       "DB_URL": "jdbc:postgresql://127.0.0.1:5432/devdb",
                       "DB_AUTH_URL": "jdbc:postgresql://127.0.0.1:5432/devdb"}
        self.source = ("127.0.0.1", 5432, "devdb")
        self.tools = {name: name for name in ("psql", "pg_dump", "pg_restore", "createdb", "dropdb")}
        self.state = {"owner": "joulemv-agent-run", "repo": str(self.root),
                      "dbName": "jmv_ai_" + "a" * 14, "dbCreated": False}

    def test_toolchain_discovery_timeouts_allow_configured_java(self):
        (self.root / "pom.xml").write_text("<java.version>21</java.version>")
        home = self.root / "jdk"
        (home / "bin").mkdir(parents=True)
        (home / "bin/java").touch()

        def probe(args, **kwargs):
            self.assertGreater(kwargs.get("timeout", 0), 0)
            if args[0] in ("brew", "/usr/libexec/java_home"):
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return subprocess.CompletedProcess(args, 0, "", 'openjdk version "21.0.1"')

        with patch.dict(agent_run.os.environ, {"JAVA_HOME": str(home)}, clear=True), \
                patch.object(agent_run.shutil, "which", side_effect=lambda name: "brew" if name == "brew" else None), \
                patch.object(agent_run.Path, "is_file", return_value=True), \
                patch.object(agent_run.subprocess, "run", side_effect=probe) as run:
            self.assertEqual(agent_run.java_env()["JAVA_HOME"], str(home))
        self.assertEqual([call.args[0][0] for call in run.call_args_list],
                         ["brew", "/usr/libexec/java_home", str(home / "bin/java")])

    def test_websynco_frontend_is_rejected_before_environment_or_database_access(self):
        (self.root / "AGENT.md").write_text("WebSynco React guidance")
        (self.root / "package.json").write_text('{"name":"reactfront"}')
        (self.root / "package-lock.json").write_text('{}')
        with patch.object(agent_run, "load_env") as load_env, \
                patch.object(agent_run, "source_database") as source_database, \
                patch.object(agent_run, "run") as run:
            with self.assertRaisesRegex(agent_run.LaunchError, "JouleMV worktree root"):
                agent_run.preflight()
        load_env.assert_not_called()
        source_database.assert_not_called()
        run.assert_not_called()

    def test_timed_out_java_version_skips_to_working_candidate(self):
        (self.root / "pom.xml").write_text("<java.version>21</java.version>")
        stalled_home, working_home = self.root / "stalled", self.root / "working"

        def probe(args, **kwargs):
            if args[0] == "/usr/libexec/java_home":
                return subprocess.CompletedProcess(args, 0, str(working_home), "")
            if args[0] == str(stalled_home / "bin/java"):
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return subprocess.CompletedProcess(args, 0, "", 'openjdk version "21.0.1"')

        with patch.dict(agent_run.os.environ, {"JAVA_HOME": str(stalled_home)}, clear=True), \
                patch.object(agent_run.shutil, "which", return_value=None), \
                patch.object(agent_run.Path, "is_file", return_value=True), \
                patch.object(agent_run.subprocess, "run", side_effect=probe):
            self.assertEqual(agent_run.java_env()["JAVA_HOME"], str(working_home))

    def test_all_java_version_timeouts_report_missing_toolchain(self):
        (self.root / "pom.xml").write_text("<java.version>21</java.version>")
        with patch.dict(agent_run.os.environ, {"JAVA_HOME": str(self.root / "stalled")}, clear=True), \
                patch.object(agent_run.shutil, "which", return_value=None), \
                patch.object(agent_run.Path, "is_file", return_value=True), \
                patch.object(agent_run.subprocess, "run", side_effect=subprocess.TimeoutExpired("java", 5)):
            with self.assertRaisesRegex(agent_run.LaunchError, "Java 21 is required"):
                agent_run.java_env()

    def test_process_stamp_timeout_returns_unavailable_identity(self):
        def probe(args, **kwargs):
            self.assertGreater(kwargs.get("timeout", 0), 0)
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])

        with patch.object(agent_run.subprocess, "run", side_effect=probe):
            self.assertEqual(agent_run.process_stamp(42), "")

    def test_cleanup_never_signals_group_with_unavailable_or_reused_identity(self):
        for saved, current in (("original", "different"), ("", "now"), (None, "now")):
            with self.subTest(saved=saved, current=current), \
                    patch.object(agent_run, "process_group", return_value={"42": current}), \
                    patch.object(agent_run.os, "killpg") as killpg:
                with self.assertRaisesRegex(agent_run.LaunchError, "identity"):
                    agent_run.stop_process(42, saved)
                killpg.assert_not_called()

    def test_cleanup_probe_failure_after_term_does_not_force_kill(self):
        with patch.object(agent_run, "process_group", side_effect=[
                {"42": "original"}, agent_run.LaunchError("Cannot verify process group identity")]), \
                patch.object(agent_run.os, "killpg") as killpg:
            with self.assertRaisesRegex(agent_run.LaunchError, "identity"):
                agent_run.stop_process(42, "original")
        killpg.assert_called_once_with(42, agent_run.signal.SIGTERM)

    def test_stop_preserves_state_when_process_identity_is_unavailable(self):
        state = {**self.state, "runId": "a" * 32, "backendPid": 42, "backendStamp": "original"}
        agent_run.write_state(state)
        with patch.object(agent_run, "process_group", return_value={"43": "unrecorded child"}), \
                patch.object(agent_run.os, "killpg") as signal, \
                patch.object(agent_run, "drop_agent_db") as drop:
            with self.assertRaisesRegex(agent_run.LaunchError, "identity"):
                agent_run.stop()
        signal.assert_not_called()
        drop.assert_not_called()
        self.assertEqual(agent_run.read_state(), state)

    def test_dead_leader_with_recorded_child_is_cleaned(self):
        with patch.object(agent_run, "process_group", side_effect=[{"43": "child"}, {}]), \
                patch.object(agent_run.os, "killpg") as killpg:
            agent_run.stop_process(42, "leader", {"42": "leader", "43": "child"})
        killpg.assert_called_once_with(42, agent_run.signal.SIGTERM)

    def test_surviving_child_is_force_killed_after_leader_exits(self):
        snapshots = [{"42": "leader", "43": "child"}] + [{"43": "child"}] * 51 + [{}]
        with patch.object(agent_run, "process_group", side_effect=snapshots), \
                patch.object(agent_run.time, "sleep"), \
                patch.object(agent_run.os, "killpg") as killpg:
            agent_run.stop_process(42, "leader")
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(42, agent_run.signal.SIGTERM), (42, agent_run.signal.SIGKILL)])

    def test_surviving_group_prevents_successful_cleanup_receipt(self):
        with patch.object(agent_run, "process_group", return_value={"42": "leader"}), \
                patch.object(agent_run.time, "sleep"), \
                patch.object(agent_run.os, "killpg"):
            with self.assertRaisesRegex(agent_run.LaunchError, "did not exit"):
                agent_run.stop_process(42, "leader")

    def test_real_orphan_child_is_stopped_using_saved_group_identity(self):
        # Bounded idle Python processes, not application servers or database fixtures.
        program = ("import subprocess,sys,time; "
                   "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=subprocess.DEVNULL); "
                   "print(child.pid,flush=True); time.sleep(30)")
        process = subprocess.Popen([sys.executable, "-c", program], start_new_session=True,
                                   stdout=subprocess.PIPE, text=True)
        members = {}
        try:
            child_pid = process.stdout.readline().strip()
            self.assertTrue(child_pid.isdigit())
            members = agent_run.process_group(process.pid)
            self.assertIn(child_pid, members)
            stamp = agent_run.process_stamp(process.pid)
            process.terminate()
            process.wait(timeout=5)
            self.assertIn(child_pid, agent_run.process_group(process.pid))
            agent_run.stop_process(process.pid, stamp, members)
            self.assertEqual(agent_run.process_group(process.pid), {})
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            if members:
                agent_run.stop_process(process.pid, members=members)

    def test_process_group_ignores_zombies_and_normalizes_stamps(self):
        result = subprocess.CompletedProcess([], 0,
            " 42 42 S Thu Oct  1 12:00:00 2026\n 43 42 Z Thu Oct  1 12:00:00 2026\n 44 44 S Thu Oct  1 12:00:00 2026\n", "")
        with patch.object(agent_run.subprocess, "run", return_value=result):
            self.assertEqual(agent_run.process_group(42), {"42": "Thu Oct 1 12:00:00 2026"})

    def test_start_missing_identity_persists_pid_and_continues_cleanup(self):
        def unavailable_stamp(pid):
            persisted = agent_run.read_state()
            self.assertEqual(persisted["backendPid"], pid)
            self.assertEqual(persisted["phase"], "starting")
            return ""

        with patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                patch.object(agent_run, "run", return_value="head"), \
                patch.object(agent_run, "preflight", return_value=(
                    self.values, self.source, self.tools, {"JAVA_HOME": "/test/jdk"}, 1)), \
                patch.object(agent_run, "prepare_frontend", return_value=1), \
                patch.object(agent_run, "build_backend", return_value=self.root / "app.war"), \
                patch.object(agent_run, "choose_port", return_value=8100), \
                patch.object(agent_run, "create_agent_db"), \
                patch.object(agent_run.subprocess, "Popen", return_value=Mock(pid=42)) as popen, \
                patch.object(agent_run, "process_stamp", side_effect=unavailable_stamp), \
                patch.object(agent_run, "process_group", return_value={"42": "unverified"}), \
                patch.object(agent_run.os, "killpg") as killpg, \
                patch.object(agent_run, "drop_agent_db") as drop, \
                patch.object(agent_run, "cleanup_vite") as cleanup, \
                patch.object(agent_run, "output") as output:
            with self.assertRaisesRegex(agent_run.LaunchError, "state retained for retry"):
                agent_run.start(10, 1)
        popen.assert_called_once()
        killpg.assert_not_called()
        drop.assert_called_once()
        cleanup.assert_called_once()
        output.assert_not_called()
        state = agent_run.read_state()
        self.assertEqual(state["backendPid"], 42)
        self.assertEqual(state["backendStamp"], "")
        self.assertNotEqual(state["phase"], "ready")

    def test_current_schema_restores_clone_without_tenant_queries_or_sql(self):
        with patch.object(agent_run, "database_exists", return_value=False), \
                patch.object(agent_run, "run", return_value="") as run:
            count = agent_run.create_agent_db(self.state, self.values, self.source, self.tools, "current")
        self.assertEqual([call.args[0][0] for call in run.call_args_list],
                         ["createdb", "pg_dump", "pg_restore"])
        self.assertEqual(count, 0)
        persisted = agent_run.read_state()
        self.assertEqual(persisted["dbEndpoint"], {"host": "127.0.0.1", "port": 5432, "user": "localuser", "sourceName": "devdb"})
        self.assertNotIn("DB_PASS", str(persisted))
        self.assertNotIn(self.values["DB_PASS"], str(persisted))
        self.assertTrue(persisted["cloneRestored"])
        self.assertEqual(persisted["schemaMode"], "current")
        self.assertEqual(persisted["tenantSchemasProcessed"], 0)
        self.assertFalse(persisted["migrationsVerified"])

    def test_migrate_schema_replays_sql_only_against_cloned_tenants(self):
        sql_path = self.root / "src/main/sql/update-tenant.sql"
        sql_path.parent.mkdir(parents=True)
        sql_path.write_text("SET search_path TO source_tenant;\nSELECT 42;\n")

        def run(args, **_kwargs):
            if args[0] == "psql" and "-c" in args:
                return "tenant_a\ntenant_b"
            return ""

        with patch.object(agent_run, "database_exists", return_value=False), \
                patch.object(agent_run, "run", side_effect=run) as mocked:
            count = agent_run.create_agent_db(self.state, self.values, self.source, self.tools)
        migrations = [call for call in mocked.call_args_list if call.kwargs.get("input_text")]
        self.assertEqual(count, 2)
        self.assertEqual(len(migrations), 2)
        for tenant, call in zip(("tenant_a", "tenant_b"), migrations):
            self.assertIn("SET search_path TO " + tenant + ";", call.kwargs["input_text"])
            self.assertEqual(call.args[0][call.args[0].index("-d") + 1], self.state["dbName"])
            self.assertIn("ON_ERROR_STOP=1", call.args[0])
        self.assertTrue(agent_run.read_state()["migrationsVerified"])

    def test_current_does_not_claim_restored_clone_after_restore_failure(self):
        def run(args, **_kwargs):
            if args[0] == "pg_restore":
                raise agent_run.LaunchError("restore failed")
            return ""
        with patch.object(agent_run, "database_exists", return_value=False), \
                patch.object(agent_run, "run", side_effect=run):
            with self.assertRaisesRegex(agent_run.LaunchError, "restore failed"):
                agent_run.create_agent_db(self.state, self.values, self.source, self.tools, "current")
        self.assertFalse(agent_run.read_state().get("cloneRestored", False))

    def test_failed_migration_is_not_verified(self):
        sql_path = self.root / "src/main/sql/update-tenant.sql"
        sql_path.parent.mkdir(parents=True)
        sql_path.write_text("SET search_path TO source_tenant;\nSELECT 42;\n")
        def run(args, **kwargs):
            if kwargs.get("input_text"):
                raise agent_run.LaunchError("migration failed")
            return "tenant_a" if args[0] == "psql" else ""
        with patch.object(agent_run, "database_exists", return_value=False), \
                patch.object(agent_run, "run", side_effect=run):
            with self.assertRaisesRegex(agent_run.LaunchError, "migration failed"):
                agent_run.create_agent_db(self.state, self.values, self.source, self.tools)
        self.assertFalse(agent_run.read_state()["migrationsVerified"])

    def test_cli_rejects_unknown_schema_mode_without_starting(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "start", "--schema-mode", "unknown"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_schema_mismatch_prevents_reuse(self):
        for existing_mode in ("current", None):
            with self.subTest(existing_mode=existing_mode), \
                    patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                    patch.object(agent_run, "read_state", return_value={"schemaMode": existing_mode}), \
                    patch.object(agent_run, "preflight") as preflight:
                with self.assertRaisesRegex(agent_run.LaunchError, "Schema mode changed"):
                    agent_run.start(10, 1, "migrate")
                preflight.assert_not_called()

    def test_changed_commit_prevents_reuse(self):
        with patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                patch.object(agent_run, "read_state", return_value={"schemaMode": "migrate", "commit": "old"}), \
                patch.object(agent_run, "run", return_value="new"):
            with self.assertRaisesRegex(agent_run.LaunchError, "HEAD changed"):
                agent_run.start(10, 1)

    def test_changed_commit_during_build_prevents_database_clone(self):
        with patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                patch.object(agent_run, "read_state", return_value=None), \
                patch.object(agent_run, "run", side_effect=["initial", "changed"]), \
                patch.object(agent_run, "preflight", return_value=(self.values, self.source, self.tools, {}, 1)), \
                patch.object(agent_run, "prepare_frontend"), \
                patch.object(agent_run, "build_backend"), \
                patch.object(agent_run, "create_agent_db") as clone:
            with self.assertRaisesRegex(agent_run.LaunchError, "HEAD changed during preparation"):
                agent_run.start(10, 1)
            clone.assert_not_called()

    def test_login_401_only_counts_for_explicit_backend_transport_probe(self):
        url = "http://127.0.0.1:8100/login"
        with patch.object(agent_run, "urlopen", side_effect=HTTPError(url, 401, "Unauthorized", {}, None)):
            self.assertTrue(agent_run.backend_login_ready("http://127.0.0.1:8100"))
            self.assertFalse(agent_run.http_ready(url))
            self.assertFalse(agent_run.frontend_ready(url))
        for path in ("/api/items", "/src/main.tsx", "/login?redirect=x"):
            other = "http://127.0.0.1:8100" + path
            with self.subTest(path=path), \
                    patch.object(agent_run, "urlopen", side_effect=HTTPError(other, 401, "Unauthorized", {}, None)):
                self.assertFalse(agent_run.http_ready(other, allow_login_unauthorized=True))

    def test_redirected_401_does_not_count_for_login(self):
        with patch.object(agent_run, "urlopen", side_effect=HTTPError(
                "http://127.0.0.1:8100/other", 401, "Unauthorized", {}, None)):
            self.assertFalse(agent_run.backend_login_ready("http://127.0.0.1:8100"))

    def test_frontend_requires_entire_module_graph(self):
        base = "http://127.0.0.1:5200/"
        responses = {base: Response(b'<script type="module" src="/src/main.tsx"></script>', "text/html"),
                     base + "src/main.tsx": Response(b'import "/deps/react.js";\n'),
                     base + "deps/react.js": Response(b'export const react = true;\n')}
        for code in (401, 404, 504):
            def open_broken(url, **_kwargs):
                if url.endswith("react.js"):
                    raise HTTPError(url, code, "Unavailable", {}, None)
                return responses[url]
            with self.subTest(code=code), patch.object(agent_run, "urlopen", side_effect=open_broken):
                self.assertFalse(agent_run.frontend_ready(base))
        with patch.object(agent_run, "urlopen", side_effect=lambda url, **kw: responses[url]):
            self.assertTrue(agent_run.frontend_ready(base))
        with patch.object(agent_run, "urlopen", return_value=Response(b"<html></html>", "text/html")):
            self.assertFalse(agent_run.frontend_ready(base))

    def test_status_failed_frontend_is_unhealthy_and_not_authenticated(self):
        state = {**self.state, "backendUrl": "http://127.0.0.1:8100",
                 "frontendUrl": "http://127.0.0.1:5200", "commit": "head", "schemaMode": "current"}
        captured = io.StringIO()
        with patch.object(agent_run, "read_state", return_value=state), \
                patch.object(agent_run, "alive", return_value=True), \
                patch.object(agent_run, "backend_login_ready", return_value=True), \
                patch.object(agent_run, "frontend_ready", return_value=False), \
                patch.object(agent_run, "run", return_value="head"), contextlib.redirect_stdout(captured):
            self.assertEqual(agent_run.status(), 1)
        import json
        result = json.loads(captured.getvalue())
        self.assertFalse(result["ok"])
        self.assertTrue(result["backendTransportReady"])
        self.assertFalse(result["authenticationVerified"])
        self.assertEqual(result["schemaMode"], "current")

    def test_source_database_requires_local_nonproduction_matching_urls(self):
        self.assertEqual(agent_run.source_database(self.values), self.source)
        for changes in ({"DB_URL": "jdbc:postgresql://remote.example/devdb"},
                        {"DB_URL": "jdbc:postgresql://localhost/production"},
                        {"DB_AUTH_URL": "jdbc:postgresql://127.0.0.1:5432/other"},
                        {"DB_URL": "jdbc:postgresql://127.0.0.1:5432/dev-db"}):
            with self.subTest(changes=changes), self.assertRaises(agent_run.LaunchError):
                agent_run.source_database({**self.values, **changes})

    def test_existing_database_is_never_silently_adopted(self):
        with patch.object(agent_run, "database_exists", return_value=True), patch.object(agent_run, "run") as run:
            with self.assertRaisesRegex(agent_run.LaunchError, "Refusing to reuse"):
                agent_run.create_agent_db(self.state, self.values, self.source, self.tools, "current")
            run.assert_not_called()

    def test_database_cleanup_checks_name_and_repository_ownership(self):
        endpoint = {"host": "127.0.0.1", "port": 5432, "user": "localuser", "sourceName": "devdb"}
        owned = {**self.state, "dbCreated": True, "dbEndpoint": endpoint}
        for state in ({**owned, "dbName": "devdb"},
                      {**owned, "repo": "/other"},
                      {**owned, "dbName": "unowned"},
                      {**owned, "dbEndpoint": {**endpoint, "sourceName": owned["dbName"]}}):
            with self.subTest(state=state), patch.object(agent_run, "run") as run:
                with self.assertRaisesRegex(agent_run.LaunchError, "not owned"):
                    agent_run.drop_agent_db(state, self.values, self.tools)
                run.assert_not_called()

    def test_database_cleanup_uses_pinned_endpoint_after_configuration_drift(self):
        state = {**self.state, "dbCreated": True,
                 "dbEndpoint": {"host": "127.0.0.1", "port": 5432, "user": "localuser", "sourceName": "devdb"}}
        changed = {**self.values, "DB_URL": "jdbc:postgresql://127.0.0.1:5433/other",
                   "DB_AUTH_URL": "jdbc:postgresql://127.0.0.1:5433/other"}
        with patch.object(agent_run, "run") as run, \
                patch.object(agent_run, "database_exists", return_value=False) as exists:
            agent_run.drop_agent_db(state, changed, self.tools)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-p") + 1], "5432")
        self.assertEqual(args[args.index("-U") + 1], "localuser")
        self.assertEqual(exists.call_args.args[2:], ("127.0.0.1", 5432, "localuser", state["dbName"]))

    def test_database_cleanup_retains_legacy_receipt_without_endpoint(self):
        state = {**self.state, "dbCreated": True, "runId": "a" * 32}
        agent_run.write_state(state)
        with patch.object(agent_run, "load_env", return_value=self.values), \
                patch.object(agent_run, "pg_tools", return_value=self.tools), \
                patch.object(agent_run, "run") as run:
            with self.assertRaisesRegex(agent_run.LaunchError, "legacy receipt"):
                agent_run.stop()
        run.assert_not_called()
        self.assertEqual(agent_run.read_state(), state)

    def test_database_cleanup_verifies_absence_and_retains_failed_receipt(self):
        state = {**self.state, "dbCreated": True, "runId": "a" * 32,
                 "dbEndpoint": {"host": "localhost", "port": 5432, "user": "localuser", "sourceName": "devdb"}}
        agent_run.write_state(state)
        with patch.object(agent_run, "load_env", return_value=self.values), \
                patch.object(agent_run, "pg_tools", return_value=self.tools), \
                patch.object(agent_run, "run"), \
                patch.object(agent_run, "database_exists", return_value=True):
            with self.assertRaisesRegex(agent_run.LaunchError, "still exists"):
                agent_run.stop()
        self.assertEqual(agent_run.read_state(), state)

    def test_stop_is_idempotent_and_watchdog_cannot_stop_new_run(self):
        with patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                patch.object(agent_run, "read_state", return_value=None), \
                patch.object(agent_run, "stop_process") as stop, \
                contextlib.redirect_stdout(io.StringIO()):
            agent_run.stop()
            agent_run.stop()
            stop.assert_not_called()
        with patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                patch.object(agent_run, "read_state", return_value={"runId": "new"}), \
                patch.object(agent_run, "stop_process") as stop:
            agent_run.stop(expected_run_id="old")
            stop.assert_not_called()

    def test_watchdog_expiry_stops_only_its_owned_run(self):
        state = {"runId": "owned", "expiresAt": 0}
        with patch.object(agent_run, "read_state", return_value=state), \
                patch.object(agent_run, "alive", return_value=True), \
                patch.object(agent_run, "stop") as stop:
            agent_run.watchdog("owned")
        stop.assert_called_once_with(expected_run_id="owned")

    def test_stop_removes_owned_state_after_successful_cleanup(self):
        state = {**self.state, "runId": "a" * 32}
        agent_run.write_state(state)
        with patch.object(agent_run, "lock_state", return_value=contextlib.nullcontext()), \
                patch.object(agent_run, "stop_process") as stop, \
                patch.object(agent_run, "load_env", return_value=self.values), \
                patch.object(agent_run, "pg_tools", return_value=self.tools), \
                patch.object(agent_run, "drop_agent_db") as drop, \
                patch.object(agent_run, "cleanup_vite") as cleanup, \
                contextlib.redirect_stdout(io.StringIO()):
            agent_run.stop()
        self.assertEqual(stop.call_count, 2)
        drop.assert_called_once_with(state, self.values, self.tools)
        cleanup.assert_called_once_with(state["runId"])
        self.assertFalse(agent_run.STATE_FILE.exists())

    def test_status_never_claims_ready_for_changed_head(self):
        state = {**self.state, "backendUrl": "http://127.0.0.1:8100",
                 "frontendUrl": "http://127.0.0.1:5200", "commit": "old", "schemaMode": "migrate"}
        captured = io.StringIO()
        with patch.object(agent_run, "read_state", return_value=state), \
                patch.object(agent_run, "alive", return_value=True), \
                patch.object(agent_run, "backend_login_ready", return_value=True), \
                patch.object(agent_run, "frontend_ready", return_value=True), \
                patch.object(agent_run, "run", return_value="new"), contextlib.redirect_stdout(captured):
            self.assertEqual(agent_run.status(), 1)
        import json
        self.assertFalse(json.loads(captured.getvalue())["commitMatches"])

    def test_unavailable_override_port_is_rejected(self):
        with patch.object(agent_run, "port_free", return_value=False):
            with self.assertRaisesRegex(agent_run.LaunchError, "unavailable"):
                agent_run.choose_port(5200, "5200")

    def test_vite_cache_is_private_to_each_run_and_removed(self):
        first, second = "a" * 32, "b" * 32
        first_config = agent_run.prepare_vite(first)
        second_config = agent_run.prepare_vite(second)
        first_cache = agent_run.vite_paths(first)[1]
        second_cache = agent_run.vite_paths(second)[1]
        self.assertNotEqual(first_cache, second_cache)
        self.assertIn(str(first_cache), first_config.read_text())
        self.assertIn(str(second_cache), second_config.read_text())
        first_cache.mkdir()
        second_cache.mkdir()
        agent_run.cleanup_vite(first)
        self.assertFalse(first_config.exists())
        self.assertFalse(first_cache.exists())
        self.assertTrue(second_config.exists())
        self.assertTrue(second_cache.exists())


if __name__ == "__main__":
    unittest.main()
