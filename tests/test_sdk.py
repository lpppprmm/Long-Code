"""Exercise the real SDK and Messages API serialization without network access."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent import Agent
from session import Registry, load_session

try:
    from anthropic import Anthropic, DefaultHttpxClient
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
except ImportError:
    Anthropic = None


@unittest.skipIf(Anthropic is None, "Install requirements.txt to exercise the actual SDK")
class SDKTest(unittest.TestCase):
    def test_bracketed_ipv6_no_proxy_does_not_block_client_construction(self):
        """The SDK's HTTP layer rejects '[::1]' in no_proxy; the client must still build."""
        names = ("no_proxy", "NO_PROXY", "ANTHROPIC_API_KEY", "MODEL_ID")
        saved = {name: os.environ.get(name) for name in names}
        broken = "localhost,127.0.0.1,::1,[::1]"
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = broken
        os.environ["ANTHROPIC_API_KEY"] = "offline-test"
        os.environ["MODEL_ID"] = "test-model"
        try:
            client = Agent().get_client()
            self.assertIsNotNone(client)
            self.assertEqual(os.environ["no_proxy"], broken)  # environment stays untouched
            client.close()
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_real_sdk_tool_roundtrip_and_context_recovery(self):
        requests = []

        def transport(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(request.url.path, "/v1/messages")
            step = len(requests)
            if step in (1, 3):
                return httpx.Response(400, json={"type": "error", "error": {
                    "type": "invalid_request_error", "message": "prompt is too long",
                }})
            if step == 5:
                content = [{"type": "tool_use", "id": "tool_1", "name": "write_file",
                            "input": {"path": "sdk.txt", "content": "SDK roundtrip verified"}}]
                stop = "tool_use"
            else:
                text = "Summary of known facts" if step == 2 else "# Session Handoff\n\n## Next Actions\n\nWrite sdk.txt."
                content = [{"type": "text", "text": text if step < 5 else "Done"}]
                stop = "end_turn"
            return httpx.Response(200, json={
                "id": f"msg_{step}", "type": "message", "role": "assistant",
                "model": "test-model", "content": content, "stop_reason": stop,
                "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 20},
            })

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = base / "repo"
            repo.mkdir()
            project = Registry(base / "data").create("SDK", str(repo))
            session = load_session(project)
            with Anthropic(api_key="offline-test", base_url="https://offline.invalid",
                           max_retries=0,
                           http_client=DefaultHttpxClient(transport=httpx.MockTransport(transport))) as client:
                Agent(client, "test-model", emit=lambda _: None).run(project, session, "Write sdk.txt")
            self.assertEqual(len(requests), 6)
            self.assertEqual(session.id, 2)
            self.assertEqual(session.active_request, "")
            self.assertEqual((repo / "sdk.txt").read_text(), "SDK roundtrip verified")
            self.assertEqual(requests[-1]["messages"][-1]["content"][0]["tool_use_id"], "tool_1")
            self.assertEqual(len(requests[4]["messages"]), 1)
            self.assertNotIn("tools", requests[1])
            self.assertNotIn("tools", requests[3])


if __name__ == "__main__":
    unittest.main()
