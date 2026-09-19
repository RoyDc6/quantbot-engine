from types import SimpleNamespace

from core import nvidia_nim_client


def test_client_sends_non_thinking_json_contract(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{
                    "message": {
                        "content": '{"sentiment_score":0,"event_type":"none","summary":"neutral","confidence":50}'
                    },
                    "finish_reason": "stop",
                }]
            },
            text="",
        )

    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(nvidia_nim_client.requests, "post", fake_post)
    monkeypatch.setattr(nvidia_nim_client.NvidiaNimClient, "_wait_for_rate_slot", staticmethod(lambda: None))

    client = nvidia_nim_client.NvidiaNimClient()
    reply = client.chat(
        "prompt",
        model="test-model",
        max_tokens=160,
        temperature=0.0,
        timeout=15,
        response_format={"type": "json_object"},
        chat_template_kwargs={"enable_thinking": False},
    )

    assert reply.startswith("{")
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["json"]["max_tokens"] == 160
    assert captured["timeout"] == 15


def test_client_rejects_incomplete_completion(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(
        nvidia_nim_client.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}]
            },
            text="",
        ),
    )
    monkeypatch.setattr(nvidia_nim_client.NvidiaNimClient, "_wait_for_rate_slot", staticmethod(lambda: None))

    reply = nvidia_nim_client.NvidiaNimClient().chat("prompt", model="test-model")

    assert reply == "[ERROR] NVIDIA NIM incomplete response (finish_reason=length)"


def test_client_requires_external_credential(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(nvidia_nim_client, "resolve_nvidia_api_key", lambda: "")

    reply = nvidia_nim_client.NvidiaNimClient().chat("prompt", model="test-model")

    assert reply == "[ERROR] NVIDIA_API_KEY is not configured"
