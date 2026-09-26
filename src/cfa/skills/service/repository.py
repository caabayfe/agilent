import re

from cfa.db import Pool
from cfa.skills.service.models import Instrument, KbArticle, KbSearchResult, ServiceHistory, ServiceVisit

_WORD = re.compile(r"[a-z0-9]{2,}")
_ERROR_CODE = re.compile(r"\bE-\d{3}\b", re.IGNORECASE)
MAX_ARTICLES = 3


class ServiceRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def history(self, customer_id: str | None = None, serial: str | None = None) -> ServiceHistory:
        async with self._pool.connection() as conn:
            instruments = await (
                await conn.execute(
                    "SELECT serial, customer_id, model, name, installed_on, warranty_until FROM service.instruments "
                    "WHERE (%(customer)s::text IS NULL OR customer_id = %(customer)s) "
                    "AND (%(serial)s::text IS NULL OR serial = %(serial)s) ORDER BY serial",
                    {"customer": customer_id, "serial": serial},
                )
            ).fetchall()
            result = []
            for instrument in instruments:
                visits = await (
                    await conn.execute(
                        "SELECT visit_id, visit_date, visit_type, summary, engineer FROM service.visits "
                        "WHERE serial = %s ORDER BY visit_date DESC",
                        (instrument["serial"],),
                    )
                ).fetchall()
                result.append(
                    Instrument.model_validate(
                        {**instrument, "visits": [ServiceVisit.model_validate(v) for v in visits]}
                    )
                )
        return ServiceHistory(instruments=result)

    async def instrument(self, serial: str) -> ServiceHistory | None:
        history = await self.history(serial=serial)
        return history if history.instruments else None

    async def search_kb(self, query: str) -> KbSearchResult:
        """Full-text search (OR of query words) boosted by exact error-code match.

        Words are reduced to ``[a-z0-9]`` before building the tsquery, so the
        model-supplied query cannot inject tsquery syntax.
        """
        words = _WORD.findall(query.lower())
        codes = [code.upper() for code in _ERROR_CODE.findall(query)]
        tsquery = " | ".join(words) if words else "none"
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT kb_id, title, applies_to, error_codes, body, escalate, "
                    "ts_rank(search, to_tsquery('english', %(q)s)) "
                    "+ CASE WHEN error_codes && %(codes)s THEN 1 ELSE 0 END AS score "
                    "FROM service.kb_articles "
                    "WHERE search @@ to_tsquery('english', %(q)s) OR error_codes && %(codes)s "
                    "ORDER BY score DESC LIMIT %(limit)s",
                    {"q": tsquery, "codes": codes, "limit": MAX_ARTICLES},
                )
            ).fetchall()
        return KbSearchResult(articles=[KbArticle.model_validate(row) for row in rows])
