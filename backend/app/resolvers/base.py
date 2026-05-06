from __future__ import annotations

from app.models import SourceCandidate


class BaseResolver:
    def __init__(self, enabled: bool = False, timeout: float = 12.0) -> None:
        self.enabled = enabled
        self.timeout = timeout

    async def search(self, *_args, **_kwargs) -> list[SourceCandidate]:
        # BASE keyword access usually requires registration/IP authorization.
        # Keep this provider as an explicit optional hook instead of scraping.
        return []
