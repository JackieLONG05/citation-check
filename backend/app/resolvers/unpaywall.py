from __future__ import annotations

import httpx

from app.models import FullTextLocation

from .utils import normalize_doi


class UnpaywallResolver:
    def __init__(self, email: str, timeout: float = 12.0) -> None:
        self.email = email
        self.timeout = timeout

    async def find_full_text(self, doi: str) -> FullTextLocation | None:
        normalized = normalize_doi(doi)
        url = f"https://api.unpaywall.org/v2/{normalized}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, params={"email": self.email})
            if response.status_code == 404:
                return None
            response.raise_for_status()

        data = response.json()
        location = data.get("best_oa_location") or data.get("first_oa_location") or {}
        pdf_url = location.get("url_for_pdf")
        landing_url = location.get("url_for_landing_page")
        license_value = location.get("license")
        if pdf_url:
            return FullTextLocation(provider="Unpaywall", url=pdf_url, kind="pdf", license=license_value)
        if landing_url:
            return FullTextLocation(provider="Unpaywall", url=landing_url, kind="landing_page", license=license_value)
        return None

