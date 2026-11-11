"""LLM Router - Provider-agnostic LLM abstraction.

Supports OpenAI, Google Gemini, Anthropic, Ollama (local), and OpenRouter.
Includes native httpx REST fallbacks for Gemini and Anthropic when SDKs are not installed.
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)
_KEY_PARAM_RE = re.compile(r"([?&](?:key|api_key)=)[^&\s'\"]+", re.IGNORECASE)


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict = None


class LLMRouter:
    """Routes chat and streaming LLM calls to the configured provider."""

    ENV_KEY_MAP = {
        "openai": ("OPENAI_API_KEY",),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
    }

    DEFAULT_MODELS = {
        "openai": "gpt-4o-mini",
        "gemini": "gemini-3.1-flash-lite",
        "anthropic": "claude-3-5-sonnet-latest",
        "ollama": "llama3.1",
        "openrouter": "openai/gpt-4o-mini",
    }

    GEMINI_FALLBACK_MODELS = (
        "gemini-3.1-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3-flash-preview",
    )

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str = "",
    ):
        self.provider = (provider or "openai").strip().lower()
        self.raw_api_key = (api_key or "").strip()
        self.model = self._sanitize_model(model)
        self.base_url = (base_url or "").strip()
        self.api_key = self._resolve_api_key()

    def _sanitize_model(self, raw_model: str) -> str:
        """Normalize user-entered model names and guard against pasted labels or wrong-provider defaults."""
        default_model = self.DEFAULT_MODELS.get(self.provider, "gpt-4o-mini")
        model = (raw_model or "").strip()
        if not model:
            return default_model

        if self.provider == "gemini" and model.startswith("models/"):
            model = model[len("models/"):].strip()

        # Catch UI mistakes like pasting an API key nickname ("Default Gemini API Key")
        if any(ch.isspace() for ch in model):
            logger.warning(f"Ignoring invalid model name with whitespace ({model!r}); using {default_model}")
            return default_model

        lower = model.lower()
        if self.provider == "gemini" and lower.startswith(("gpt-", "o1", "o3", "o4", "claude-", "llama", "openai/")):
            return default_model
        if self.provider == "openai" and lower.startswith(("gemini-", "gemma-", "claude-")):
            return default_model
        if self.provider == "anthropic" and lower.startswith(("gpt-", "gemini-", "gemma-", "openai/")):
            return default_model
        if self.provider == "ollama" and (lower in ("gpt-4", "gpt-4o-mini") or lower.startswith(("gemini-", "claude-"))):
            return default_model
        if self.provider == "openrouter" and lower == "gpt-4":
            return default_model

        return model

    @staticmethod
    def _redact_secrets(text: str) -> str:
        return _KEY_PARAM_RE.sub(r"\1[REDACTED]", str(text or ""))

    @classmethod
    def _build_http_error(cls, resp: httpx.Response, body_bytes: Optional[bytes] = None) -> RuntimeError:
        raw_text = (
            body_bytes.decode("utf-8", "replace")
            if body_bytes is not None
            else (resp.text or "")
        )
        detail = raw_text.strip()
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                err = parsed.get("error")
                if isinstance(err, dict) and err.get("message"):
                    detail = str(err["message"])
                elif parsed.get("message"):
                    detail = str(parsed["message"])
                elif parsed.get("detail"):
                    detail = str(parsed["detail"])
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                err = parsed[0].get("error")
                if isinstance(err, dict) and err.get("message"):
                    detail = str(err["message"])
        except Exception:
            pass
        detail = cls._redact_secrets(detail)[:320] or f"HTTP {resp.status_code}"
        return RuntimeError(f"HTTP {resp.status_code} ({resp.reason_phrase}): {detail}")

    def _resolve_api_key(self) -> str:
        if self.raw_api_key and self.raw_api_key.lower() != "mock":
            return self.raw_api_key
        for env_name in self.ENV_KEY_MAP.get(self.provider, ()):
            val = (os.environ.get(env_name) or "").strip()
            if val:
                return val
        return self.raw_api_key

    def is_configured(self) -> bool:
        """Whether enough configuration exists to attempt a live LLM request."""
        if self.provider == "ollama":
            return True
        return bool(self.api_key and self.api_key.lower() not in {"mock", "test-key"})

    def configuration_issue(self) -> Optional[str]:
        if self.provider == "ollama":
            return None
        if not self.api_key:
            return f"No API key configured for '{self.provider}'."
        if self.api_key.lower() == "mock":
            return "Placeholder 'mock' API key configured."
        return None

    def _ensure_credentials(self):
        if self.provider != "ollama" and not self.api_key:
            raise ValueError(
                f"No API key configured for provider '{self.provider}'. "
                "Add an API key in Settings or switch to local Ollama."
            )

    def _get_openai_client(self):
        from openai import AsyncOpenAI

        if self.provider == "ollama":
            url = self.base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
            return AsyncOpenAI(base_url=url, api_key="ollama", timeout=60.0)
        if self.provider == "openrouter":
            url = self.base_url or "https://openrouter.ai/api/v1"
            return AsyncOpenAI(base_url=url, api_key=self.api_key, timeout=60.0)
        if self.base_url:
            return AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=60.0)
        return AsyncOpenAI(api_key=self.api_key, timeout=60.0)

    @staticmethod
    def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        for msg in messages:
            role = (msg.get("role") or "user").lower()
            if role not in ("user", "assistant", "system"):
                role = "assistant" if role in ("agent", "model", "bot") else "user"
            content = str(msg.get("content") or "").strip()
            raw_atts = msg.get("attachments") or []
            valid_atts = [a for a in raw_atts if isinstance(a, dict)] if isinstance(raw_atts, list) else []
            if not content and valid_atts:
                content = "Please inspect and analyze the attached file(s)."
            if content or valid_atts:
                item: Dict[str, Any] = {"role": role, "content": content}
                if valid_atts:
                    item["attachments"] = valid_atts
                cleaned.append(item)
        return cleaned

    @staticmethod
    def _has_binary_media(messages: List[Dict[str, Any]]) -> bool:
        return any(
            isinstance(att, dict) and bool(att.get("data_base64"))
            for msg in messages
            for att in (msg.get("attachments") or [])
        )

    @staticmethod
    def _format_openai_messages(messages: List[Dict[str, Any]], system_prompt: str = "") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if system_prompt:
            out.append({"role": "system", "content": system_prompt})
        for msg in messages:
            atts = msg.get("attachments") or []
            image_atts = [a for a in atts if a.get("is_image") and a.get("data_base64")]
            if not image_atts:
                out.append({"role": msg["role"], "content": msg["content"]})
                continue
            parts: List[Dict[str, Any]] = [{"type": "text", "text": msg["content"] or "Inspect the attached image(s)."}]
            for att in image_atts:
                mime = att.get("llm_mime_type") or att.get("mime_type") or "image/png"
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{att['data_base64']}"},
                })
            out.append({"role": msg["role"], "content": parts})
        return out

    @staticmethod
    def _format_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        anth_messages: List[Dict[str, Any]] = []
        for m in messages:
            if m["role"] not in ("user", "assistant"):
                continue
            atts = m.get("attachments") or []
            media_atts = [
                a for a in atts
                if a.get("data_base64") and (a.get("is_image") or a.get("mime_type") == "application/pdf")
            ]
            if not media_atts:
                anth_messages.append({"role": m["role"], "content": m["content"]})
                continue
            blocks: List[Dict[str, Any]] = []
            for att in media_atts:
                mime = att.get("llm_mime_type") or att.get("mime_type") or "image/png"
                if mime == "application/pdf":
                    blocks.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": att["data_base64"]},
                    })
                else:
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": att["data_base64"]},
                    })
            blocks.append({"type": "text", "text": m["content"] or "Analyze the attached file(s)."})
            anth_messages.append({"role": m["role"], "content": blocks})
        if anth_messages and anth_messages[0]["role"] != "user":
            anth_messages = [{"role": "user", "content": "Hello Desk Officer."}] + anth_messages
        return anth_messages

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request to the configured LLM provider."""
        self._ensure_credentials()
        norm_messages = self._normalize_messages(messages)
        try:
            if self.provider in ("openai", "ollama", "openrouter"):
                return await self._chat_openai(norm_messages, system_prompt, temperature, max_tokens)
            elif self.provider == "gemini":
                return await self._chat_gemini(norm_messages, system_prompt, temperature, max_tokens)
            elif self.provider == "anthropic":
                return await self._chat_anthropic(norm_messages, system_prompt, temperature, max_tokens)
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        except Exception as e:
            logger.error(f"LLM call failed ({self.provider}/{self.model}): {self._redact_secrets(str(e))}")
            raise

    async def _chat_openai(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        client = self._get_openai_client()
        all_messages = self._format_openai_messages(messages, system_prompt)

        response = await client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            content=response.choices[0].message.content or "",
            model=self.model,
            provider=self.provider,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    def _gemini_candidates(self) -> List[str]:
        models = [self.model]
        for fallback_model in self.GEMINI_FALLBACK_MODELS:
            if fallback_model not in models:
                models.append(fallback_model)
        return models

    def _gemini_endpoint(self, model_name: str, action: str) -> str:
        clean = model_name[len("models/"):] if model_name.startswith("models/") else model_name
        base = (self.base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        if "/v1beta" in base or "/v1alpha" in base or base.endswith("/v1"):
            return f"{base}/models/{clean}:{action}"
        return f"{base}/v1beta/models/{clean}:{action}"

    @staticmethod
    def _build_gemini_payload(messages, system_prompt: str, temperature: float, max_tokens: int) -> dict:
        contents = []
        for msg in messages:
            parts: List[Dict[str, Any]] = []
            if msg.get("content"):
                parts.append({"text": msg["content"]})
            for att in msg.get("attachments") or []:
                b64 = att.get("data_base64")
                mime = att.get("llm_mime_type") or att.get("mime_type") or ""
                if b64 and (att.get("is_image") or mime == "application/pdf"):
                    parts.append({
                        "inlineData": {
                            "mimeType": mime or "image/png",
                            "data": b64,
                        }
                    })
            if not parts:
                parts.append({"text": "Please analyze the attached file(s)."})
            contents.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": parts,
            })
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        return payload

    async def _chat_gemini(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        # Direct Gemini REST API v1beta via httpx using x-goog-api-key header (never expose key in URLs/logs)
        payload = self._build_gemini_payload(messages, system_prompt, temperature, max_tokens)
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        has_media = self._has_binary_media(messages)
        timeout = httpx.Timeout(
            connect=10.0,
            read=45.0 if has_media else 22.0,
            write=30.0 if has_media else 15.0,
            pool=10.0,
        )

        last_error: Optional[Exception] = None
        candidates = self._gemini_candidates()
        async with httpx.AsyncClient(timeout=timeout) as client:
            for idx, model_name in enumerate(candidates):
                url = self._gemini_endpoint(model_name, "generateContent")
                has_next = idx + 1 < len(candidates)
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                except httpx.TimeoutException as exc:
                    last_error = RuntimeError(f"Gemini request timed out on '{model_name}': {exc}")
                    if has_next:
                        logger.warning(f"Gemini timeout on {model_name}; failing over to next model")
                        continue
                    raise last_error from exc

                if not resp.is_success:
                    err = self._build_http_error(resp)
                    if resp.status_code in (404, 503) and has_next:
                        logger.warning(f"Gemini {resp.status_code} on {model_name}; failing over: {err}")
                        last_error = err
                        continue
                    raise err

                data = resp.json()
                res_candidates = data.get("candidates") or []
                parts = (((res_candidates[0].get("content") or {}).get("parts")) or []) if res_candidates else []
                text = "".join(p.get("text", "") for p in parts if not p.get("thought") and p.get("text"))
                self.model = model_name
                return LLMResponse(content=text, model=model_name, provider=self.provider)

        raise last_error or RuntimeError("Gemini request failed on all candidate models")

    async def _chat_anthropic(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        # Anthropic requires user-first alternating messages
        anth_messages = self._format_anthropic_messages(messages)

        try:
            from anthropic import AsyncAnthropic  # type: ignore

            client = AsyncAnthropic(api_key=self.api_key)
            response = await client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt if system_prompt else "",
                messages=anth_messages,
                temperature=temperature,
            )
            return LLMResponse(
                content=response.content[0].text,
                model=self.model,
                provider=self.provider,
                usage={
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                },
            )
        except ImportError:
            pass

        # Direct Anthropic Messages REST API via httpx
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anth_messages,
        }
        if system_prompt:
            payload["system"] = system_prompt

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            if not resp.is_success:
                raise self._build_http_error(resp)
            data = resp.json()

        text = "".join(
            block.get("text", "")
            for block in (data.get("content") or [])
            if block.get("type") == "text"
        )
        return LLMResponse(content=text, model=self.model, provider=self.provider)

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion response token by token."""
        self._ensure_credentials()
        norm_messages = self._normalize_messages(messages)
        try:
            if self.provider in ("openai", "ollama", "openrouter"):
                async for chunk in self._stream_openai(norm_messages, system_prompt, temperature, max_tokens):
                    yield chunk
            elif self.provider == "anthropic":
                async for chunk in self._stream_anthropic(norm_messages, system_prompt, temperature, max_tokens):
                    yield chunk
            elif self.provider == "gemini":
                async for chunk in self._stream_gemini(norm_messages, system_prompt, temperature, max_tokens):
                    yield chunk
            else:
                response = await self.chat(norm_messages, system_prompt, temperature, max_tokens)
                if response.content:
                    yield response.content
        except Exception as e:
            logger.error(f"LLM stream failed ({self.provider}/{self.model}): {self._redact_secrets(str(e))}")
            raise

    async def _stream_openai(self, messages, system_prompt, temperature, max_tokens):
        client = self._get_openai_client()
        all_messages = self._format_openai_messages(messages, system_prompt)

        stream = await client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def _stream_gemini(self, messages, system_prompt, temperature, max_tokens):
        payload = self._build_gemini_payload(messages, system_prompt, temperature, max_tokens)
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        has_media = self._has_binary_media(messages)
        timeout = httpx.Timeout(
            connect=10.0,
            read=45.0 if has_media else 22.0,
            write=30.0 if has_media else 15.0,
            pool=10.0,
        )

        last_error: Optional[Exception] = None
        candidates = self._gemini_candidates()
        async with httpx.AsyncClient(timeout=timeout) as client:
            for idx, model_name in enumerate(candidates):
                url = self._gemini_endpoint(model_name, "streamGenerateContent")
                has_next = idx + 1 < len(candidates)
                emitted_any = False
                try:
                    async with client.stream("POST", url, headers=headers, params={"alt": "sse"}, json=payload) as resp:
                        if not resp.is_success:
                            err_bytes = await resp.aread()
                            err = self._build_http_error(resp, err_bytes)
                            if resp.status_code in (404, 503) and has_next:
                                logger.warning(f"Gemini stream {resp.status_code} on {model_name}; failing over: {err}")
                                last_error = err
                                continue
                            raise err

                        self.model = model_name
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                chunk_data = json.loads(data_str)
                                res_candidates = chunk_data.get("candidates") or []
                                parts = (((res_candidates[0].get("content") or {}).get("parts")) or []) if res_candidates else []
                                for p in parts:
                                    if not p.get("thought") and p.get("text"):
                                        emitted_any = True
                                        yield p["text"]
                            except json.JSONDecodeError:
                                continue
                        return
                except httpx.TimeoutException as exc:
                    if emitted_any or not has_next:
                        raise RuntimeError(f"Gemini stream timed out on '{model_name}': {exc}") from exc
                    logger.warning(f"Gemini stream timeout on {model_name}; failing over to next model")
                    last_error = exc
                    continue

        if last_error:
            raise last_error

    async def _stream_anthropic(self, messages, system_prompt, temperature, max_tokens):
        anth_messages = self._format_anthropic_messages(messages)

        try:
            from anthropic import AsyncAnthropic  # type: ignore

            client = AsyncAnthropic(api_key=self.api_key)
            async with client.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system_prompt if system_prompt else "",
                messages=anth_messages,
                temperature=temperature,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
            return
        except ImportError:
            pass

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anth_messages,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", "https://api.anthropic.com/v1/messages", headers=headers, json=payload) as resp:
                if not resp.is_success:
                    err_bytes = await resp.aread()
                    raise self._build_http_error(resp, err_bytes)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        ev = json.loads(data_str)
                        if ev.get("type") == "content_block_delta":
                            delta_text = (ev.get("delta") or {}).get("text")
                            if delta_text:
                                yield delta_text
                    except json.JSONDecodeError:
                        continue
