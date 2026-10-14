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
from unittest.mock import patch
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

    def test_current_schema_restores_clone_without_tenant_queries_or_sql(self):
        with patch.object(agent_run, "database_exists", return_value=False), \
                patch.object(agent_run, "run", return_value="") as run:
            count = agent_run.create_agent_db(self.state, self.values, self.source, self.tools, "current")
        self.assertEqual([call.args[0][0] for call in run.call_args_list],
                         ["createdb", "pg_dump", "pg_restore"])
        self.assertEqual(count, 0)
        persisted = agent_run.read_state()
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
        for state in ({**self.state, "dbName": "devdb", "dbCreated": True},
                      {**self.state, "repo": "/other", "dbCreated": True},
                      {**self.state, "dbName": "unowned", "dbCreated": True}):
            with self.subTest(state=state), patch.object(agent_run, "run") as run:
                with self.assertRaises(agent_run.LaunchError):
                    agent_run.drop_agent_db(state, self.values, self.source, self.tools)
                run.assert_not_called()

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

    def test_process_stamp_mismatch_is_not_killed(self):
        with patch.object(agent_run.os, "kill") as kill, \
                patch.object(agent_run.os, "killpg") as killpg, \
                patch.object(agent_run, "process_stamp", return_value="different"):
            agent_run.stop_process(42, "original")
        kill.assert_called_once_with(42, 0)
        killpg.assert_not_called()

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
        drop.assert_called_once_with(state, self.values, self.source, self.tools)
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
