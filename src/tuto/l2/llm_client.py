"""Thin OpenAI-compatible chat client for the L2 support judge.

Kept minimal and dependency-free (httpx only, like the Cito backend). Points at the uniapi
OpenAI-compatible endpoint; the model defaults to gpt-4o-mini, the cheapest route that
reliably returns structured JSON for this binary-ish classification. Note: new Claude
models reject a `temperature` field, but gpt-4o-mini accepts temperature=0, which we want
for reproducible labels, so it is sent only for non-Claude models.
"""

from __future__ import annotations

import json
import os
import time

import httpx


class LLMClient:
    def __init__(self, model: str | None = None, timeout: float = 60.0):
        self.base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("L2_MODEL", "gpt-4o-mini")
        if not self.base or not self.key:
            raise ValueError("LLM_BASE_URL / LLM_API_KEY not set")
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {self.key}"}, timeout=timeout
        )

    def judge_json(self, system: str, user: str, max_tokens: int = 400) -> dict | None:
        """Return the model's JSON object, or None on unrecoverable failure.

        max_tokens must cover REASONING too: uniapi's Claude models think inside the same
        budget, and a 400-token cap can be fully consumed by reasoning, returning an empty
        content (observed with claude-sonnet-5: 8 of 13 arbiter calls came back blank).
        Callers using a reasoning model should pass a few thousand.
        """
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
        }
        if not self.model.startswith("claude"):
            body["temperature"] = 0
        for attempt in range(4):
            try:
                r = self.client.post(f"{self.base}/chat/completions", json=body)
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(2**attempt)
                    continue
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"] or ""
                content = content.strip()
                if content.startswith("```"):  # some models fence JSON despite response_format
                    content = content.strip("`").removeprefix("json").strip()
                data = json.loads(content)
                if isinstance(data, list):  # some models wrap the object in an array
                    data = next((x for x in data if isinstance(x, dict)), None)
                if not isinstance(data, dict):
                    raise ValueError("model returned non-object JSON")
                return data
            except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValueError):
                if attempt == 3:
                    return None
                time.sleep(2**attempt)
        return None

    def close(self) -> None:
        self.client.close()
