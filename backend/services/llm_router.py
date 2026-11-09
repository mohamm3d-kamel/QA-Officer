"""LLM Router - Provider-agnostic LLM abstraction.

Supports OpenAI, Google Gemini, Anthropic, and Ollama (local).
"""
import json
import logging
from typing import Optional, AsyncGenerator
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    usage: dict = None


class LLMRouter:
    """Routes LLM calls to the configured provider."""

    def __init__(self, provider: str, api_key: str, model: str):
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_openai_client(self):
        if self.provider == "ollama":
            from openai import AsyncOpenAI
            return AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        else:
            from openai import AsyncOpenAI
            return AsyncOpenAI(api_key=self.api_key)

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Send a chat completion request to the configured LLM provider."""
        try:
            if self.provider in ("openai", "ollama"):
                return await self._chat_openai(messages, system_prompt, temperature, max_tokens)
            elif self.provider == "gemini":
                return await self._chat_gemini(messages, system_prompt, temperature, max_tokens)
            elif self.provider == "anthropic":
                return await self._chat_anthropic(messages, system_prompt, temperature, max_tokens)
            else:
                raise ValueError(f"Unsupported LLM provider: {self.provider}")
        except Exception as e:
            logger.error(f"LLM call failed ({self.provider}): {e}")
            raise

    async def _chat_openai(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        client = self._get_openai_client()
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

        response = await client.chat.completions.create(
            model=self.model,
            messages=all_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            content=response.choices[0].message.content,
            model=self.model,
            provider=self.provider,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            },
        )

    async def _chat_gemini(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)

        model = genai.GenerativeModel(
            self.model,
            system_instruction=system_prompt if system_prompt else None,
        )

        # Convert messages to Gemini format
        gemini_messages = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            gemini_messages.append({"role": role, "parts": [msg["content"]]})

        response = await model.generate_content_async(
            gemini_messages,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        return LLMResponse(
            content=response.text,
            model=self.model,
            provider=self.provider,
        )

    async def _chat_anthropic(self, messages, system_prompt, temperature, max_tokens) -> LLMResponse:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=self.api_key)

        response = await client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt if system_prompt else "",
            messages=messages,
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

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion response."""
        try:
            if self.provider in ("openai", "ollama"):
                async for chunk in self._stream_openai(messages, system_prompt, temperature, max_tokens):
                    yield chunk
            elif self.provider == "anthropic":
                async for chunk in self._stream_anthropic(messages, system_prompt, temperature, max_tokens):
                    yield chunk
            else:
                # Fallback: non-streaming for providers that don't support it easily
                response = await self.chat(messages, system_prompt, temperature, max_tokens)
                yield response.content
        except Exception as e:
            logger.error(f"LLM stream failed ({self.provider}): {e}")
            raise

    async def _stream_openai(self, messages, system_prompt, temperature, max_tokens):
        client = self._get_openai_client()
        all_messages = []
        if system_prompt:
            all_messages.append({"role": "system", "content": system_prompt})
        all_messages.extend(messages)

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

    async def _stream_anthropic(self, messages, system_prompt, temperature, max_tokens):
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=self.api_key)

        async with client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt if system_prompt else "",
            messages=messages,
            temperature=temperature,
        ) as stream:
            async for text in stream.text_stream:
                yield text

