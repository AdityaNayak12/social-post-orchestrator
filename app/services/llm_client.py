from groq import Groq, RateLimitError, APIError
from app.config import settings
from app.core.exception import TransientError, DeterministicError
from app.core.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = "Shorten and transform this text into a catchy Instagram caption. Add relevant hashtags. Keep it concise."


class LLMClient:
    def __init__(self, model: str = "llama-3.3-70b-versatile"):
        self.client = Groq(api_key=settings.GROQ_API_KEY)
        self.model = model

    def transform_caption(self, text: str) -> str:
        if not text or not text.strip():
            raise DeterministicError("Empty text provided", stage="llm_transform")

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
            return response.choices[0].message.content.strip()
        except RateLimitError:
            logger.warning("Groq rate limit exceeded")
            raise TransientError("Groq rate limit exceeded", stage="llm_transform")
        except APIError as e:
            logger.error(f"Groq API error: {e}")
            raise TransientError(f"Groq API error: {e}", stage="llm_transform")


llm_client = LLMClient()
