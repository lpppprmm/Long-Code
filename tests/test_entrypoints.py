"""Exercise package entry points and .env resolution outside the source directory."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class EntryPointTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.checkout = self.base / "checkout"
        self.checkout.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1] / "long_code",
                        self.checkout / "long_code", ignore=shutil.ignore_patterns("__pycache__"))
        self.data = self.base / "data"
        (self.checkout / ".env").write_text(
            f"SIMPLE_AGENT_HOME={self.data}\nMODEL_ID=from-dotenv\n"
            "CONTEXT_LIMIT_TOKENS=2000\nMAX_TOKENS=123\n", encoding="utf-8",
        )
        self.env = {key: value for key, value in os.environ.items() if key not in {
            "SIMPLE_AGENT_HOME", "MODEL_ID", "ANTHROPIC_API_KEY", "CONTEXT_LIMIT_TOKENS",
            "CONTEXT_LIMIT", "MAX_TOKENS", "MAX_TOOL_OUTPUT", "SUMMARY_MAX_TOKENS",
            "PYTHON_DOTENV_DISABLED",
        }}
        self.env.update(PYTHONPATH=str(self.checkout), ANTHROPIC_API_KEY="")

    def run_python(self, *args, input=""):
        result = subprocess.run([sys.executable, *args], cwd=self.base, env=self.env,
                                input=input, capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_cli_reads_checkout_env_from_another_directory(self):
        result = self.run_python("-m", "long_code", input="/current\n/exit\n")
        self.assertIn("No project selected.", result.stdout)
        self.assertTrue((self.data / ".lock").exists())
        self.assertFalse((self.base / ".env").exists())

    def test_api_import_is_lazy_and_exported_settings_override_dotenv(self):
        self.env["MAX_TOKENS"] = "456"
        self.run_python("-c", "\n".join([
            "from pathlib import Path",
            "import code",
            "assert hasattr(code, 'InteractiveConsole')",
            "from long_code.api import app, create_app",
            "assert app.instance is None",
            f"assert not Path({str(self.data)!r}).exists()",
            "api = create_app()",
            "runtime = api.state.workspace.runtime",
            "try:",
            f"    assert runtime.registry.home == Path({str(self.data)!r})",
            "    assert runtime.agent.context_limit == 8000",
            "    assert runtime.agent.max_tokens == 456",
            "    assert runtime.agent.model == 'from-dotenv'",
            "    assert runtime.agent.client is None",
            "finally:",
            "    runtime.close()",
        ]))
