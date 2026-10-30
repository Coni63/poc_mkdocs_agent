import os
from typing import AsyncIterator, Protocol

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()


class LLM(Protocol):
    def stream(self, system: str, docs: str, history: list[dict], question: str) -> AsyncIterator[str]:
        """Stream the answer. The prompt prefix (system + docs + history) must stay stable for caching."""
        ...

    async def complete_json(self, system: str, docs: str, history: list[dict], question: str) -> str:
        """Non-streaming call that must return a JSON object (as text)."""
        ...


class DeepSeekLLM:
    """DeepSeek via its OpenAI-compatible API. Prefix caching is automatic on identical prompt prefixes."""

    def __init__(self, model: str = "deepseek-chat"):
        key = os.getenv("AGENT_KEY")
        if not key:
            raise RuntimeError("AGENT_KEY manquant dans .env")
        self.model = model
        self.client = AsyncOpenAI(api_key=key, base_url="https://api.deepseek.com")

    @staticmethod
    def _messages(system: str, docs: str, history: list[dict], question: str) -> list[dict]:
        return [
            {"role": "system", "content": f"{system}\n\n{docs}"},
            *history,
            {"role": "user", "content": question},
        ]

    async def complete_json(self, system: str, docs: str, history: list[dict], question: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, docs, history, question),
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    async def stream(self, system: str, docs: str, history: list[dict], question: str) -> AsyncIterator[str]:
        messages = self._messages(system, docs, history, question)
        response = await self.client.chat.completions.create(model=self.model, messages=messages, stream=True)
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
