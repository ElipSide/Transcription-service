"""DeepSeek report generation, ported from the bot's ReportGenerator."""
import logging

from openai import AsyncOpenAI

from ..config import settings
from .prompts import (
    prompt_ai_extract,
    prompt_analysis,
    role_ai_extract,
    role_analysis,
)

logger = logging.getLogger("transcribe.deepseek")


class ReportGenerator:
    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        return self._client

    async def _req(self, role: str, prompt: str, timeout: int = 180) -> str | None:
        try:
            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": role},
                    {"role": "user", "content": prompt},
                ],
                stream=False,
                timeout=timeout,
            )
            return response.choices[0].message.content
        except Exception as e:  # noqa: BLE001
            logger.error("DeepSeek request failed: %s", e)
            return None

    async def business_report_html(self, dialog_text: str) -> str | None:
        """Returns an HTML business report (mode=report)."""
        prompt = f"{prompt_analysis}\n\nТекст диалога для анализа:\n{dialog_text}"
        return await self._req(role_analysis, prompt)

    async def ai_extract(self, dialog_text: str) -> str | None:
        """Returns a condensed bullet summary (mode=ai_extract)."""
        prompt = f"{prompt_ai_extract}\n{dialog_text}"
        return await self._req(role_ai_extract, prompt)


report_generator = ReportGenerator()
