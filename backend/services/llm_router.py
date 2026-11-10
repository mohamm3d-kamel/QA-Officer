"""LLM Router - Provider-agnostic LLM abstraction.

Supports OpenAI, Google Gemini, Anthropic, and Ollama (local).
"""
import asyncio
import logging
from typing import Any
from dataclasses import dataclass
import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict = None


class LLMRouter:
    """Routes LLM calls to the configured provider with pooled clients and transient retries."""

    def __init__(self, provider: str, api_key: str, model: str):
        self.provider = (provider or "openai").lower()
        self.api_key = (api_key or "").strip()
        self.model = (model or "gpt-4").strip()
        self._openai_client = None

    def _get_openai_client(self):
        if self._openai_client is None:
            from openai import AsyncOpenAI
            if self.provider == "ollama":
                self._openai_client = AsyncOpenAI(
                    base_url="http://localhost:11434/v1",
                    api_key="ollama",
                    timeout=60.0,
                )
            else:
                self._openai_client = AsyncOpenAI(
                    api_key=self.api_key,
                    timeout=60.0,
                )
        return self._openai_client

    @staticmethod
    def _is_non_retryable(err: Exception) -> bool:
        if isinstance(err, ValueError):
            return True
        status_code = getattr(err, "status_code", None)
        if status_code is None and isinstance(err, httpx.HTTPStatusError) and err.response is not None:
            status_code = err.response.status_code
        if status_code in (400, 401, 403, 404, 422):
            return True
        msg = str(err).lower()
        return any(
            token in msg
            for token in (
                "incorrect api key",
                "invalid api key",
                "missing credentials",
                "not configured",
                "401",
                "authentication",
                "permission",
            )
        )

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Send a chat completion request to the configured LLM provider."""
        if self.provider != "ollama" and (not self.api_key or not self.api_key.strip() or self.api_key.strip() == "mock"):
            raise ValueError(f"LLM API key is not configured for provider '{self.provider}'.")

        last_err: Exception | None = None
        for attempt in range(2):
            try:
                if self.provider in ("openai", "ollama"):
                    return await self._chat_openai(messages, system_prompt, temperature, max_tokens, json_mode)
                elif self.provider == "gemini":
                    return await self._chat_gemini(messages, system_prompt, temperature, max_tokens, json_mode)
                elif self.provider == "anthropic":
                    return await self._chat_anthropic(messages, system_prompt, temperature, max_tokens)
                else:
                    raise ValueError(f"Unsupported LLM provider: {self.provider}")
            except Exception as e:
                last_err = e
                if attempt == 0 and not self._is_non_retryable(e):
                    logger.warning(f"Transient LLM error ({self.provider}), retrying once: {e}")
                    await asyncio.sleep(1.0)
                    continue
                logger.error(f"LLM call failed ({self.provider}): {e}")
                raise
        raise last_err  # pragma: no cover

    async def _chat_openai(self, messages, system_prompt, temperature, max_tokens, json_mode: bool = False) -> LLMResponse:
        client = self._get_openai_client()
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            # Some local Ollama or legacy OpenAI models reject response_format
            if json_mode and "response_format" in str(e).lower():
                kwargs.pop("response_format", None)
                response = await client.chat.completions.create(**kwargs)
            else:
                raise

        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=self.model,
            provider=self.provider,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    async def _chat_gemini(self, messages, system_prompt, temperature, max_tokens, json_mode: bool = False) -> LLMResponse:
        model_name = self.model.removeprefix("models/")
        if not model_name.startswith(("gemini", "gemma")):
            model_name = "gemini-3-flash-preview"

        contents = [
            {
                "role": "user" if msg.get("role") == "user" else "model",
                "parts": [{"text": str(msg.get("content", ""))}],
            }
            for msg in messages
        ]

        gen_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": gen_config,
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        ai_studio_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        vertex_global_url = f"https://aiplatform.googleapis.com/v1/publishers/google/models/{model_name}:generateContent"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                ai_studio_url,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
            # Support Vertex AI Express AQ. keys if AI Studio rejects auth/scope
            if resp.status_code in (400, 401, 403) and self.api_key.startswith("AQ."):
                vertex_resp = await client.post(
                    vertex_global_url,
                    headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                    json=payload,
                )
                if vertex_resp.is_success:
                    resp = vertex_resp

        if not resp.is_success:
            err_msg = resp.text[:400]
            try:
                err_msg = resp.json().get("error", {}).get("message", err_msg)
            except Exception:
                pass
            raise httpx.HTTPStatusError(
                f"Gemini API error ({resp.status_code}): {err_msg}",
                request=resp.request,
                response=resp,
            )

        data = resp.json()
        candidates = data.get("candidates") or []
        parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
        text_chunks = [p["text"] for p in parts if isinstance(p, dict) and p.get("text") and not p.get("thought")]
        if not text_chunks:
            text_chunks = [p["text"] for p in parts if isinstance(p, dict) and p.get("text")]

        usage_meta = data.get("usageMetadata") or {}
        return LLMResponse(
            content="".join(text_chunks),
            model=model_name,
            provider=self.provider,
            usage={
                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            },
        )

    async def _chat_anthropic(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "user" if m.get("role") == "user" else "assistant", "content": str(m.get("content", ""))}
                for m in messages
            ],
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if not resp.is_success:
            err_msg = resp.text[:400]
            try:
                err_msg = resp.json().get("error", {}).get("message", err_msg)
            except Exception:
                pass
            raise httpx.HTTPStatusError(
                f"Anthropic API error ({resp.status_code}): {err_msg}",
                request=resp.request,
                response=resp,
            )

        data = resp.json()
        text = "".join(
            block.get("text", "")
            for block in (data.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return LLMResponse(
            content=text,
            model=self.model,
            provider=self.provider,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
        )

