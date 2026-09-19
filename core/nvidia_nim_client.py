"""QuantBot-owned NVIDIA NIM client.

This module deliberately lives inside E:/quant so WorkBuddy upgrades cannot
change QuantBot's model request contract. Credentials are read from the process
environment or the current user's Windows environment registry; no API key is
stored in source code.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Optional

import requests


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_NIM_RATE_LIMIT_PER_MINUTE = 9
_MIN_INTERVAL_SECONDS = 60.0 / NVIDIA_NIM_RATE_LIMIT_PER_MINUTE
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_STARTED = 0.0


def resolve_nvidia_api_key() -> str:
    """Resolve the NVIDIA key without embedding it in project source."""
    value = os.environ.get("NVIDIA_API_KEY", "").strip()
    if value:
        return value

    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, _ = winreg.QueryValueEx(key, "NVIDIA_API_KEY")
            return str(value or "").strip()
        except (FileNotFoundError, OSError):
            pass
    return ""


class NvidiaNimClient:
    """Minimal OpenAI-compatible client with QuantBot's fail-safe contract."""

    def __init__(self, base_url: str = NVIDIA_NIM_BASE_URL):
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _timeout(value: Optional[float]) -> float:
        if value is None:
            value = os.environ.get("NVIDIA_NIM_TIMEOUT_SECONDS", 120)
        try:
            return max(float(value), 0.1)
        except (TypeError, ValueError):
            return 120.0

    @staticmethod
    def _wait_for_rate_slot() -> None:
        global _LAST_REQUEST_STARTED
        with _RATE_LOCK:
            wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _LAST_REQUEST_STARTED)
            if wait > 0:
                time.sleep(wait)
            _LAST_REQUEST_STARTED = time.monotonic()

    def chat(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 200,
        temperature: float = 0.0,
        timeout: Optional[float] = None,
        response_format: Optional[Dict[str, Any]] = None,
        chat_template_kwargs: Optional[Dict[str, Any]] = None,
    ) -> str:
        api_key = resolve_nvidia_api_key()
        if not api_key:
            return "[ERROR] NVIDIA_API_KEY is not configured"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if response_format:
            payload["response_format"] = response_format
        if chat_template_kwargs:
            payload["chat_template_kwargs"] = chat_template_kwargs

        self._wait_for_rate_slot()
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=self._timeout(timeout),
            )
        except requests.RequestException as exc:
            return f"[ERROR] {exc}"

        if response.status_code != 200:
            return f"[ERROR {response.status_code}] {response.text[:500]}"

        try:
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = str(message.get("content") or "").strip()
            finish_reason = choice.get("finish_reason")
        except (TypeError, ValueError, AttributeError, IndexError) as exc:
            return f"[ERROR] NVIDIA NIM malformed response: {exc}"

        if finish_reason != "stop":
            return f"[ERROR] NVIDIA NIM incomplete response (finish_reason={finish_reason})"
        if not content:
            return "[ERROR] NVIDIA NIM returned empty content"
        return content


nvidia_llm = NvidiaNimClient()
