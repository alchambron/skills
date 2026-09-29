#!/usr/bin/env python3
"""Regression checks for the review runner's Vite startup gate."""

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest


spec = importlib.util.spec_from_file_location("agent_run", Path(__file__).with_name("agent_run.py"))
agent_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_run)


class ModuleHandler(BaseHTTPRequestHandler):
    broken = False

    def do_GET(self):
        if self.path == "/":
            body = b'<html><script type="module" src="/src/main.tsx"></script></html>'
            content_type = "text/html"
        elif self.path == "/src/main.tsx":
            body = b'import "/deps/react.js";\n'
            content_type = "text/javascript"
        elif self.path == "/deps/react.js":
            if self.broken:
                self.send_error(504, "Outdated Optimize Dep")
                return
            body = b'export const react = true;\n'
            content_type = "text/javascript"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class AgentRunTests(unittest.TestCase):
    def test_frontend_requires_nested_optimized_javascript(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), ModuleHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = "http://127.0.0.1:%s/" % server.server_port
            try:
                ModuleHandler.broken = True
                self.assertFalse(agent_run.frontend_ready(url))
                ModuleHandler.broken = False
                self.assertTrue(agent_run.frontend_ready(url))
            finally:
                server.shutdown()
                thread.join()

    def test_vite_cache_is_private_to_each_run_and_removed(self):
        original_root = agent_run.ROOT
        with tempfile.TemporaryDirectory() as temporary:
            agent_run.ROOT = Path(temporary)
            (agent_run.ROOT / "frontend").mkdir()
            try:
                first = "a" * 32
                second = "b" * 32
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
            finally:
                agent_run.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
