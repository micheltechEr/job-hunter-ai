import re
import json
import logging
from typing import Type, TypeVar, Optional
from pydantic import BaseModel
from openai import AsyncOpenAI
from app.config import settings
from app.services.rate_limiter import execute_with_llm_protection

logger = logging.getLogger("job_hunter.llm_service")

T = TypeVar("T", bound=BaseModel)

class LLMService:
    def __init__(self):
        # Fallback key to avoid startup crashes if env variables are empty initially
        api_key = settings.LLM_API_KEY or "placeholder-key"
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.LLM_BASE_URL,
            timeout=getattr(settings, "LLM_TIMEOUT", 35.0)
        )
        self._supports_structured_parse = True

    def get_model_candidates(self) -> list[str]:
        primary = settings.LLM_MODEL
        fallbacks = [m.strip() for m in getattr(settings, "LLM_FALLBACK_MODELS", "").split(",") if m.strip()]
        candidates = []
        if primary:
            candidates.append(primary)
        for fb in fallbacks:
            if fb not in candidates:
                candidates.append(fb)
        return candidates or ["antigravity/gemini-3.8-flash-tiered", "gpt-4o-mini"]

    async def get_json_completion(self, system_prompt: str, user_prompt: str) -> dict:
        """Calls LLM and guarantees a JSON dict response with rate limit protection, timeout protection & automatic model failover."""
        models = self.get_model_candidates()
        last_exception = None

        for idx, model_name in enumerate(models):
            async def _call():
                return await self.client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )

            try:
                response = await execute_with_llm_protection(_call)
                raw_content = response.choices[0].message.content or ""
                # Clean markdown code block if model wrapped JSON in ```json ... ```
                cleaned = raw_content.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                    cleaned = re.sub(r"\s*```$", "", cleaned)
                return json.loads(cleaned)
            except Exception as e:
                last_exception = e
                err_msg = str(e).lower()
                is_cooldown_or_limit = any(term in err_msg for term in ["model_cooldown", "cooling down", "429", "rate_limit", "timeout"])
                if is_cooldown_or_limit and idx < len(models) - 1:
                    next_model = models[idx + 1]
                    logger.warning(f"Model '{model_name}' hit cooldown/rate-limit ({e}). Failing over immediately to '{next_model}'...")
                    continue

                logger.error(f"Error in LLM JSON completion with model '{model_name}': {e}")
                # Dynamic fallback: try to extract JSON with regex if parsing failed
                if 'raw_content' in locals() and raw_content:
                    try:
                        match = re.search(r"\{.*\}", raw_content, re.DOTALL)
                        if match:
                            return json.loads(match.group(0))
                    except Exception:
                        pass
                if idx == len(models) - 1:
                    raise e

        if last_exception:
            raise last_exception

    async def get_structured_output(self, system_prompt: str, user_prompt: str, response_schema: Type[T]) -> T:
        """Acquires structured output validating against Pydantic schema with rate limit protection."""
        if self._supports_structured_parse:
            async def _call_structured():
                return await self.client.beta.chat.completions.parse(
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format=response_schema,
                    temperature=0.1
                )

            try:
                response = await execute_with_llm_protection(_call_structured)
                if response and response.choices and response.choices[0].message.parsed:
                    return response.choices[0].message.parsed
            except Exception as e:
                self._supports_structured_parse = False
                logger.info(f"Structured outputs parser unavailable on proxy/model ({e}). Switched to JSON completion permanently.")

        schema_json = json.dumps(response_schema.model_json_schema())
        system_prompt_with_schema = (
            f"{system_prompt}\n\nIMPORTANT: Your response must be valid JSON matching this schema:\n{schema_json}"
        )
        parsed_json = await self.get_json_completion(system_prompt_with_schema, user_prompt)
        return response_schema.model_validate(parsed_json)

    async def get_text_completion(self, system_prompt: str, user_prompt: str, temperature: float = 0.5) -> str:
        """Fetches plain text completion from the LLM with rate limit protection and model failover."""
        models = self.get_model_candidates()
        last_exception = None

        for idx, model_name in enumerate(models):
            async def _call_text():
                return await self.client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=temperature
                )

            try:
                response = await execute_with_llm_protection(_call_text)
                return response.choices[0].message.content or ""
            except Exception as e:
                last_exception = e
                err_msg = str(e).lower()
                is_cooldown_or_limit = any(term in err_msg for term in ["model_cooldown", "cooling down", "429", "rate_limit", "timeout"])
                if is_cooldown_or_limit and idx < len(models) - 1:
                    next_model = models[idx + 1]
                    logger.warning(f"Text completion model '{model_name}' hit cooldown/rate-limit ({e}). Failing over to '{next_model}'...")
                    continue
                logger.error(f"Error fetching text completion from LLM with model '{model_name}': {e}")
                if idx == len(models) - 1:
                    raise e

        if last_exception:
            raise last_exception

llm_service = LLMService()
