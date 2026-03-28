from httpx import Timeout
from groq import Groq, RateLimitError, APIError, APITimeoutError
from app.config import settings
from app.core.exception import TransientError, DeterministicError
from app.core.logger import get_logger
from app.core.retry import retry_on_transient

logger = get_logger(__name__)

SYSTEM_PROMPT = "Shorten and transform this text into a catchy Instagram caption. Add relevant hashtags. Keep it concise."


class LLMClient:
    def __init__(self, model: str = "llama-3.3-70b-versatile"):
        self.client = Groq(
            api_key=settings.GROQ_API_KEY,
            timeout=Timeout(settings.GROQ_TIMEOUT_SECONDS)
        )
        self.model = model
        self.max_retries = settings.GROQ_MAX_RETRIES

    def transform_caption(self, text: str) -> str:
        if not text or not text.strip():
            raise DeterministicError("Empty text provided", stage="llm_transform")

        return retry_on_transient(
            lambda: self._call_api(text),
            max_retries=self.max_retries,
            backoff="exponential",
            exponential_base=2.0,
            jitter=True,
            on_retry=lambda attempt, sleep_time, _exc: logger.warning(
                f"Transient error, retrying in {sleep_time:.2f}s (attempt {attempt + 1}/{self.max_retries + 1})"
            ),
            on_exhausted=lambda _exc: logger.error(
                f"Max retries ({self.max_retries}) exceeded for LLM call"
            ),
        )

    def _call_api(self, text: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text}
                ],
                temperature=0.7,
                max_tokens=500
            )
            content = response.choices[0].message.content
            if not content:
                raise TransientError("Empty response from LLM", stage="llm_transform")
            return content.strip()
        except RateLimitError as e:
            logger.warning(f"Groq rate limit exceeded: {e}")
            raise TransientError("Groq rate limit exceeded", stage="llm_transform")
        except APITimeoutError as e:
            logger.warning(f"Groq request timed out: {e}")
            raise TransientError("Groq request timed out", stage="llm_transform")
        except APIError as e:
            logger.error(f"Groq API error: {e}")
            raise TransientError(f"Groq API error: {e}", stage="llm_transform")


llm_client = LLMClient()
