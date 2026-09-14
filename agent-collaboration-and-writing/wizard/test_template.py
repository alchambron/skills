"""Isolated regression checks. Run: python3 -m unittest discover -s <wizard-dir>."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


TEMPLATE = Path(__file__).with_name("template.sh").resolve()


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wizard-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env_file = self.root / ".env"
        self.env = dict(os.environ, ENV_FILE=str(self.env_file), WIZARD_REPO="")

    def run_shell(self, body, input_text=""):
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$1"; ' + body, "wizard-test", str(TEMPLATE)],
            input=input_text, text=True, capture_output=True,
            cwd=self.root, env=self.env, timeout=10,
        )

    def test_eof_aborts_all_prompts_before_writes(self):
        for prompt in ('ask VALUE prompt', 'ask_secret VALUE prompt', 'pause',
                       'if confirm prompt; then :; fi'):
            with self.subTest(prompt=prompt):
                result = self.run_shell(prompt + '; write_env VALUE bad')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("input ended", result.stdout)
                self.assertFalse(self.env_file.exists())

    def test_empty_required_input_aborts(self):
        for helper in ("ask", "ask_secret"):
            with self.subTest(helper=helper):
                result = self.run_shell(f'{helper} VALUE prompt; write_env VALUE "$VALUE"', "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.env_file.exists())

    def test_enter_reuses_literal_value(self):
        for helper in ("ask", "ask_secret"):
            with self.subTest(helper=helper):
                self.env_file.write_text("VALUE='with # spaces $literal \\\"quotes\\\"'\n")
                result = self.run_shell(f'{helper} VALUE prompt; printf "VALUE:%s" "$VALUE"', "\n")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('VALUE:with # spaces $literal', result.stdout)

    def test_existing_unquoted_value(self):
        self.env_file.write_text("VALUE=old-value\n")
        result = self.run_shell('ask VALUE prompt; [[ "$VALUE" == old-value ]]', "\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_read_failure_aborts(self):
        self.env_file.write_text("VALUE=old\n")
        result = self.run_shell('grep() { return 2; }; ask VALUE prompt; write_env VALUE bad', "new\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.env_file.read_text(), "VALUE=old\n")

    def test_write_failure_preserves_original_even_in_conditional(self):
        original = "KEEP=original\nVALUE=old\n"
        self.env_file.write_text(original)
        result = self.run_shell('grep() { return 2; }; if write_env VALUE new; then exit 99; fi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file.read_text(), original)
        self.assertFalse(list(self.root.glob(".env.tmp.*")))

    def test_reject_symlink_and_preserve_target(self):
        target = self.root / "shared.env"
        target.write_text("KEEP=original\n")
        self.env_file.symlink_to(target)
        result = self.run_shell("write_env VALUE new")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.env_file.is_symlink())
        self.assertEqual(target.read_text(), "KEEP=original\n")

    def test_reject_dangling_symlink(self):
        target = self.root / "missing.env"
        self.env_file.symlink_to(target)
        result = self.run_shell("write_env VALUE new")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.env_file.is_symlink())
        self.assertFalse(target.exists())

    def test_invalid_values_do_not_modify_or_create_file(self):
        for value in ("", "one\ntwo", "one\rtwo", "can't"):
            for exists in (False, True):
                with self.subTest(value=value, exists=exists):
                    self.env_file.unlink(missing_ok=True)
                    if exists:
                        self.env_file.write_text("KEEP=original\n")
                    self.env["TEST_VALUE"] = value
                    result = self.run_shell('write_env VALUE "$TEST_VALUE"')
                    self.assertNotEqual(result.returncode, 0)
                    if exists:
                        self.assertEqual(self.env_file.read_text(), "KEEP=original\n")
                    else:
                        self.assertFalse(self.env_file.exists())

    def test_invalid_key_is_rejected(self):
        result = self.run_shell("write_env 'BAD[' value")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.env_file.exists())

    def test_roundtrip_and_upsert_preserve_other_values(self):
        self.env_file.write_text("KEEP=original\nVALUE=old\nVALUE=duplicate\nLAST=end")
        self.env["TEST_VALUE"] = 'spaces # hash $literal `literal` "quote" \\slash'
        result = self.run_shell('write_env VALUE "$TEST_VALUE"; [[ "$(_existing VALUE)" == "$TEST_VALUE" ]]')
        self.assertEqual(result.returncode, 0, result.stderr)
        content = self.env_file.read_text()
        self.assertIn("KEEP=original\n", content)
        self.assertIn("LAST=end\n", content)
        self.assertEqual(sum(line.startswith("VALUE=") for line in content.splitlines()), 1)
        self.assertEqual(self.env_file.stat().st_mode & 0o777, 0o600)

    def test_upsert_when_grep_has_no_surviving_lines(self):
        self.env_file.write_text("VALUE=old\n")
        result = self.run_shell("write_env VALUE new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file.read_text(), "VALUE='new'\n")

    def test_repeated_write_is_byte_identical(self):
        self.env_file.write_text("KEEP=original\n")
        first = self.run_shell("write_env VALUE new")
        self.assertEqual(first.returncode, 0, first.stderr)
        content = self.env_file.read_bytes()
        second = self.run_shell("write_env VALUE new")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.env_file.read_bytes(), content)

    def test_failed_move_preserves_original(self):
        self.env_file.write_text("KEEP=original\n")
        result = self.run_shell('mv() { return 1; }; if write_env VALUE new; then exit 99; fi')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.env_file.read_text(), "KEEP=original\n")
        self.assertFalse(list(self.root.glob(".env.tmp.*")))

    def test_no_confirmation_skips_and_reaches_summary(self):
        result = self.run_shell('if confirm proceed; then write_env VALUE bad; else note "Skipped"; fi; finish', "n\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipped", result.stdout)
        self.assertIn("Setup complete", result.stdout)
        self.assertFalse(self.env_file.exists())

    def test_no_browser_fallback_is_visible(self):
        result = self.run_shell('PATH=/nonexistent; open_url https://example.test/setup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("visit it manually: https://example.test/setup", result.stdout)

    def test_failed_browser_fallback_is_visible(self):
        result = self.run_shell('PATH=/nonexistent; wslview() { return 1; }; open_url https://example.test/setup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("visit it manually", result.stdout)

    def test_explicit_github_target_and_secret_stdin(self):
        result = self.run_shell('''
            gh() {
              if [[ "$1" == auth ]]; then return 0; fi
              printf '%s\\n' "$@" >> gh-args
              if [[ "$1" == secret ]]; then cat > secret-input; fi
            }
            WIZARD_REPO=example/target
            set_secret TOKEN secret-value
            set_var SETTING public-value
        ''')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.root / "gh-args").read_text()
        self.assertEqual(args.count("--repo\nexample/target\n"), 2)
        self.assertNotIn("secret-value", args)
        self.assertEqual((self.root / "secret-input").read_text(), "secret-value")

    def test_empty_values_never_call_github(self):
        for call in ('set_secret TOKEN ""', 'set_var SETTING ""'):
            with self.subTest(call=call):
                result = self.run_shell('gh() { touch gh-called; }; ' + call)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "gh-called").exists())

    def test_invalid_repo_skips_writes_and_reaches_summary(self):
        for repo in ("", "missing-owner", "owner/repo/extra", "owner/repo with spaces"):
            with self.subTest(repo=repo):
                self.env["WIZARD_REPO"] = repo
                result = self.run_shell('''
                    gh() { touch gh-called; }
                    write_env LOCAL saved
                    set_secret TOKEN value
                    set_var SETTING value
                    finish
                ''')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.root / "gh-called").exists())
                self.assertIn("Setup complete", result.stdout)
                self.assertIn("wrote 1 value(s)", result.stdout)
                self.assertIn("GitHub secret TOKEN (set WIZARD_REPO", result.stdout)
                self.assertIn("GitHub variable SETTING (set WIZARD_REPO", result.stdout)
                self.assertIn("LOCAL='saved'", self.env_file.read_text())

    def test_explorer_status_one_is_not_reported_as_failure(self):
        result = self.run_shell('PATH=/nonexistent; explorer.exe() { return 1; }; open_url https://example.test/setup')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("couldn't open a browser", result.stdout)
        self.assertIn("if the browser did not open, visit it manually", result.stdout)

    def test_other_opener_failures_still_warn(self):
        for opener, status in (("explorer.exe", 2), ("wslview", 1), ("xdg-open", 1), ("open", 1)):
            with self.subTest(opener=opener, status=status):
                result = self.run_shell(f'PATH=/nonexistent; {opener}() {{ return {status}; }}; open_url https://example.test/setup')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("couldn't open a browser; visit it manually", result.stdout)

    def test_github_failure_reports_manual_followup(self):
        result = self.run_shell('gh() { return 1; }; WIZARD_REPO=example/target; set_secret TOKEN value; finish')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gh secret set TOKEN --repo example/target", result.stdout)


if __name__ == "__main__":
    unittest.main()
