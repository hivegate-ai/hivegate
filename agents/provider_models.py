"""agno model classes with the gateway's retry policy made safe to turn on.

The gateway enables agno's provider-error retries (``PROVIDER_RETRY`` in
agents/model_factory.py). Two things in agno make that unsafe as-is:

* **A stream retry restarts the whole stream.** agno's ``_invoke_stream_with_retry``
  re-runs ``invoke_stream`` from the top, so an error *after* some output has already
  been yielded would send that output to the client a second time - text the caller has
  already rendered, or half a JSON answer the classifier then fails to parse.
  ``StreamRestartGuard`` lets a retry happen only while nothing has been yielded; after
  that the error is re-raised as ``StreamInterruptedError``, which is never retried.

* **Billing errors are retried.** agno's non-retryable set is 400/401/403/404/413/422,
  so DeepSeek's 402 "Insufficient Balance" and Z.ai's 429 "Insufficient balance or no
  resource package" were each tried three times with backoff - seen on 2026-09-29.

Every subclass keeps its base class's ``__name__``: agno picks a model's native
reasoning path by class name ("Claude", "Gemini", "DeepSeek", "OpenAIResponses"), so a
renamed subclass would silently lose it - see agents/claude_refusal.py.
"""

from typing import Any, AsyncIterator, Iterator

from agno.exceptions import ModelProviderError
from agno.models.deepseek import DeepSeek
from agno.models.google import Gemini
from agno.models.openai.like import OpenAILike
from agno.models.xai import xAI

# Provider messages that mean "this account cannot pay", however they are coded.
_BILLING_MARKERS = ("insufficient balance", "insufficient_quota", "used all available credits", "please recharge")


class StreamInterruptedError(ModelProviderError):
    """A provider error after output had already streamed - not retried."""

    def __init__(self, error: ModelProviderError):
        super().__init__(
            message=f"{error.message} (stream interrupted after output had been sent; not retried)",
            status_code=502,
            model_name=error.model_name,
            model_id=error.model_id,
        )


def is_billing_error(error: ModelProviderError) -> bool:
    if error.status_code == 402:
        return True
    message = str(error.message).lower()
    return any(marker in message for marker in _BILLING_MARKERS)


class StreamRestartGuard:
    """Mixin: retry a stream only before its first chunk; never retry a billing error."""

    def _is_retryable_error(self, error: ModelProviderError) -> bool:
        if isinstance(error, StreamInterruptedError) or is_billing_error(error):
            return False
        return super()._is_retryable_error(error)  # type: ignore[misc]

    def _interrupted(self, error: ModelProviderError) -> ModelProviderError:
        # Only an error agno would have retried needs converting; anything else (a
        # refusal, a bad request) keeps its own type and status.
        if self._is_retryable_error(ModelProviderError.classify(error)):
            return StreamInterruptedError(error)
        return error

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        started = False
        try:
            for chunk in super().invoke_stream(*args, **kwargs):  # type: ignore[misc]
                started = True
                yield chunk
        except ModelProviderError as e:
            if not started:
                raise
            converted = self._interrupted(e)
            if converted is e:
                raise
            raise converted from e

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        started = False
        try:
            async for chunk in super().ainvoke_stream(*args, **kwargs):  # type: ignore[misc]
                started = True
                yield chunk
        except ModelProviderError as e:
            if not started:
                raise
            converted = self._interrupted(e)
            if converted is e:
                raise
            raise converted from e


def _keep_name(cls: type, base: type) -> type:
    cls.__name__ = base.__name__
    cls.__qualname__ = base.__qualname__
    return cls


class GuardedGemini(StreamRestartGuard, Gemini):
    pass


class GuardedXAI(StreamRestartGuard, xAI):
    pass


class GuardedOpenAILike(StreamRestartGuard, OpenAILike):
    pass


class GuardedDeepSeek(StreamRestartGuard, DeepSeek):
    pass


for _cls, _base in (
    (GuardedGemini, Gemini),
    (GuardedXAI, xAI),
    (GuardedOpenAILike, OpenAILike),
    (GuardedDeepSeek, DeepSeek),
):
    _keep_name(_cls, _base)
