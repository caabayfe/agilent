from datetime import date

from pydantic import BaseModel


class ServiceVisit(BaseModel):
    visit_id: str
    visit_date: date
    visit_type: str
    summary: str
    engineer: str


class Instrument(BaseModel):
    serial: str
    customer_id: str
    model: str
    name: str
    installed_on: date
    warranty_until: date
    visits: list[ServiceVisit]


class ServiceHistory(BaseModel):
    instruments: list[Instrument]


class KbArticle(BaseModel):
    kb_id: str
    title: str
    applies_to: str
    error_codes: list[str]
    body: str
    escalate: bool


class KbSearchResult(BaseModel):
    articles: list[KbArticle]
