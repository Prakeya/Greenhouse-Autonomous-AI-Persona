"""Shared helpers for classifying Gemini API errors.

Quota/rate-limit errors (HTTP 429 / RESOURCE_EXHAUSTED) are fundamentally
different from a model genuinely judging a candidate unfit: they say nothing
about the candidate, they mean "we can't ask the model anything right now."
Treating them as an ordinary editorial rejection burns through remaining
quota one candidate at a time and mislabels good candidates as permanently
rejected in the compost heap. RateLimitedError lets callers detect this case
and back off instead.
"""


class RateLimitedError(RuntimeError):
    """Raised when the Gemini API reports a quota or rate-limit error (HTTP 429)."""


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text or "rate limit" in text.lower()
