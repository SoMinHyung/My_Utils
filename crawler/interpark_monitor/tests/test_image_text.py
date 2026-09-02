import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interpark_monitor import image_text


class ImageTextTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.image_path = Path(self.temp_dir.name) / "image with spaces.png"
        self.image_path.write_bytes(b"\x89PNG\r\n\x1a\nmock")

    @staticmethod
    def _completed(command, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=stdout,
            stderr=stderr,
        )

    def test_success_uses_chatgpt_login_and_structured_output(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            if command[1:3] == ["login", "status"]:
                return self._completed(command, stdout="Logged in using ChatGPT\n")

            output_index = command.index("--output-last-message") + 1
            schema_index = command.index("--output-schema") + 1
            output_path = Path(command[output_index])
            schema = json.loads(Path(command[schema_index]).read_text(encoding="utf-8"))
            self.assertEqual(schema["required"], ["text"])
            self.assertFalse(schema["additionalProperties"])
            output_path.write_text('{"text": " A b C 7 "}', encoding="utf-8")
            return self._completed(command)

        with (
            patch.object(image_text, "_find_codex_binary", return_value="/fake/codex"),
            patch.object(image_text.subprocess, "run", side_effect=fake_run),
            patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}),
        ):
            result = image_text.extract_image_text_with_codex(self.image_path)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "must-not-leak")

        self.assertEqual(result, "AbC7")
        self.assertEqual(len(calls), 2)

        login_command, login_kwargs = calls[0]
        self.assertEqual(login_command, ["/fake/codex", "login", "status"])
        self.assertNotIn("OPENAI_API_KEY", login_kwargs["env"])

        exec_command, exec_kwargs = calls[1]
        self.assertEqual(exec_command[1], "exec")
        self.assertEqual(exec_command[-1], "-")
        self.assertIn("--ephemeral", exec_command)
        self.assertIn("--ignore-user-config", exec_command)
        self.assertEqual(exec_command[exec_command.index("--sandbox") + 1], "read-only")
        self.assertEqual(
            exec_command[exec_command.index("--image") + 1],
            str(self.image_path.resolve()),
        )
        self.assertEqual(
            exec_command[exec_command.index("--image") + 2],
            "--output-schema",
        )
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", exec_command)
        self.assertNotIn("OPENAI_API_KEY", exec_kwargs["env"])

    def test_api_key_login_does_not_run_exec(self):
        status = self._completed(
            ["/fake/codex", "login", "status"],
            stdout="Logged in using API key\n",
        )
        with (
            patch.object(image_text, "_find_codex_binary", return_value="/fake/codex"),
            patch.object(image_text.subprocess, "run", return_value=status) as run_mock,
        ):
            result = image_text.extract_image_text_with_codex(self.image_path)

        self.assertIsNone(result)
        run_mock.assert_called_once()

    def test_timeout_returns_none(self):
        def fake_run(command, **kwargs):
            if command[1:3] == ["login", "status"]:
                return self._completed(command, stdout="Logged in using ChatGPT\n")
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with (
            patch.object(image_text, "_find_codex_binary", return_value="/fake/codex"),
            patch.object(image_text.subprocess, "run", side_effect=fake_run),
        ):
            result = image_text.extract_image_text_with_codex(
                self.image_path,
                timeout_seconds=1,
            )

        self.assertIsNone(result)

    def test_invalid_json_returns_none(self):
        def fake_run(command, **kwargs):
            if command[1:3] == ["login", "status"]:
                return self._completed(command, stdout="Logged in using ChatGPT\n")
            output_index = command.index("--output-last-message") + 1
            Path(command[output_index]).write_text("not-json", encoding="utf-8")
            return self._completed(command)

        with (
            patch.object(image_text, "_find_codex_binary", return_value="/fake/codex"),
            patch.object(image_text.subprocess, "run", side_effect=fake_run),
        ):
            result = image_text.extract_image_text_with_codex(self.image_path)

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
