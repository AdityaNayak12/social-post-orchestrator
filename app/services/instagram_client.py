import time
import logging
import requests
from app.config import settings
from app.core.exception import TransientError, DeterministicError
from app.core.retry import retry_on_transient

logger = logging.getLogger(__name__)

INSTAGRAM_API_BASE = "https://graph.facebook.com/v19.0"


def _handle_api_error(response: requests.Response, stage: str) -> None:
    if response.status_code == 429:
        raise TransientError("Instagram rate limit exceeded", stage=stage)
    if response.status_code >= 500:
        raise TransientError(f"Instagram server error: {response.status_code}", stage=stage)
    if response.status_code >= 400:
        try:
            data = response.json()
            error_msg = data.get("error", {}).get("message", "Unknown error")
        except Exception:
            error_msg = f"HTTP {response.status_code}"
        raise DeterministicError(f"Instagram API error: {error_msg}", stage=stage)


class InstagramClient:
    def __init__(self):
        self.access_token = settings.FACEBOOK_PAGE_ACCESS_TOKEN
        self.account_id = settings.INSTAGRAM_ACCOUNT_ID
        self.timeout = settings.INSTAGRAM_TIMEOUT_SECONDS
        self.max_retries = settings.INSTAGRAM_MAX_RETRIES

    def publish_post(self, caption: str, image_url: str) -> str:
        if not caption or not caption.strip():
            raise DeterministicError("Empty caption provided", stage="instagram_publish_creation")
        if not image_url or not image_url.strip():
            raise DeterministicError(
                "image_url is required for Instagram publish via /media endpoint",
                stage="instagram_publish_creation"
            )

        container_id = retry_on_transient(
            lambda: self._create_image_container(caption, image_url),
            max_retries=self.max_retries,
            backoff="exponential",
            exponential_base=2.0,
            jitter=True,
            on_retry=lambda attempt, sleep, _exc: logger.warning(
                f"Retrying Instagram container creation in {sleep:.2f}s "
                f"(attempt {attempt + 1}/{self.max_retries + 1})"
            ),
            on_exhausted=lambda _exc: logger.error(
                f"Max retries ({self.max_retries}) exceeded for Instagram container creation"
            ),
        )

        return retry_on_transient(
            lambda: self._publish_creation(container_id),
            max_retries=self.max_retries,
            backoff="exponential",
            exponential_base=2.0,
            jitter=True,
            on_retry=lambda attempt, sleep, _exc: logger.warning(
                f"Retrying Instagram media publish in {sleep:.2f}s "
                f"(attempt {attempt + 1}/{self.max_retries + 1}) for container {container_id}"
            ),
            on_exhausted=lambda _exc: logger.error(
                f"Max retries ({self.max_retries}) exceeded for Instagram media publish "
                f"for container {container_id}"
            ),
        )

    def _post_form(self, url: str, payload: dict, stage: str) -> dict:
        try:
            response = requests.post(url, params=payload, timeout=self.timeout)
        except requests.Timeout as exc:
            raise TransientError(
                f"Instagram request timed out after {self.timeout}s",
                stage=stage
            ) from exc
        except requests.ConnectionError as exc:
            raise TransientError("Instagram connection error", stage=stage) from exc
        except requests.RequestException as exc:
            raise TransientError("Instagram transport error", stage=stage) from exc

        if not response.ok:
            _handle_api_error(response, stage)

        try:
            return response.json()
        except ValueError:
            raise DeterministicError("Instagram API returned invalid JSON", stage=stage)

    def _create_image_container(self, caption: str, image_url: str) -> str:
        url = f"{INSTAGRAM_API_BASE}/{self.account_id}/media"
        payload = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token
        }

        data = self._post_form(url, payload, "instagram_publish")
        container_id = data.get("id")

        if not container_id:
            raise DeterministicError("No container ID returned", stage="instagram_publish_creation") 

        return container_id

    def _publish_creation(self, container_id: str) -> str:
        url = f"{INSTAGRAM_API_BASE}/{self.account_id}/media_publish"
        payload = {
            "creation_id": container_id,
            "access_token": self.access_token
        }

        data = self._post_form(url, payload, "instagram_publish_creation")
        post_id = data.get("id")

        if not post_id:
            raise DeterministicError("No post ID returned after publish", stage="instagram_publish_creation")

        logger.info(f"Successfully published Instagram post: {post_id}")
        return post_id


instagram_client = InstagramClient()
