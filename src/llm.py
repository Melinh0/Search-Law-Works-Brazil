"""Cliente LLM com provedor Ollama (cloud com rotacao de chaves e fallback local)."""

from __future__ import annotations

import itertools
import threading
import time

import requests

from .config import Settings


class LLMError(RuntimeError):
    """Erro de chamada ao provedor LLM apos esgotar todas as alternativas."""


class OllamaClient:
    """Chama o Ollama cloud girando as chaves disponiveis e caindo para o local."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._keys = settings.ollama_api_keys or [""]
        self._key_cycle = itertools.cycle(self._keys)
        self._lock = threading.Lock()
        self._failed_keys: set[str] = set()

    def _next_key(self) -> str:
        with self._lock:
            for _ in range(len(self._keys)):
                key = next(self._key_cycle)
                if key not in self._failed_keys:
                    return key
            return next(self._key_cycle)

    def _mark_failed(self, key: str) -> None:
        with self._lock:
            self._failed_keys.add(key)
            if len(self._failed_keys) >= len(self._keys):
                self._failed_keys.clear()

    def chat(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        timeout: int = 300,
    ) -> str:
        """Envia uma mensagem e retorna a resposta final do modelo, sem streaming."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        errors: list[str] = []
        for _ in range(max(len(self._keys), 1)):
            key = self._next_key()
            try:
                return self._call_cloud(messages, key, max_tokens, temperature, timeout)
            except LLMError as exc:
                self._mark_failed(key)
                errors.append(str(exc))
        try:
            return self._call_local(messages, max_tokens, temperature, timeout)
        except LLMError as exc:
            errors.append(str(exc))
        raise LLMError(" | ".join(errors))

    def _call_cloud(
        self,
        messages: list[dict],
        key: str,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        payload = {
            "model": self.settings.ollama_cloud_model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            resp = requests.post(
                f"{self.settings.ollama_cloud_url}/api/chat",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"cloud indisponivel: {exc}") from exc
        if resp.status_code in (401, 402, 403, 429, 500, 502, 503):
            raise LLMError(f"cloud HTTP {resp.status_code}: {resp.text[:120]}")
        if resp.status_code != 200:
            raise LLMError(f"cloud HTTP {resp.status_code}")
        data = resp.json()
        content = data.get("message", {}).get("content", "")
        if not content.strip():
            raise LLMError("cloud retornou resposta vazia")
        return content

    def _call_local(
        self,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        payload = {
            "model": self.settings.ollama_local_model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        try:
            resp = requests.post(
                f"{self.settings.ollama_host}/api/chat",
                json=payload,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"local indisponivel: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"local HTTP {resp.status_code}")
        content = resp.json().get("message", {}).get("content", "")
        if not content.strip():
            raise LLMError("local retornou resposta vazia")
        return content

    def chat_with_retry(self, *args, retries: int = 2, **kwargs) -> str:
        """Tenta a chamada varias vezes com espera crescente antes de propagar erro."""
        last: Exception | None = None
        for attempt in range(retries):
            try:
                return self.chat(*args, **kwargs)
            except LLMError as exc:
                last = exc
                time.sleep(3 * (attempt + 1))
        raise LLMError(str(last))
