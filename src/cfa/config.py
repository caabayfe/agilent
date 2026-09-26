"""Typed settings per component, loaded from environment variables.

Every secret is a ``SecretStr`` so it is never rendered in logs or reprs. Each
component only loads the settings it needs, so a skill server never sees model
keys and the BFF never sees database superuser credentials.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class _Base(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", frozen=True)


class DatabaseSettings(_Base):
    database_url: SecretStr


class IdentitySettings(_Base):
    """Where tokens come from and how the agent authenticates to the IdP."""

    idp_url: str = "http://idp:8000"
    idp_issuer: str = "http://idp:8000"
    bff_audience: str = "cfa-bff"
    agent_client_id: str = "customer-assistant"
    agent_client_secret: SecretStr


class SkillServerSettings(_Base):
    idp_url: str = "http://idp:8000"
    idp_issuer: str = "http://idp:8000"
    skill_host: str = "0.0.0.0"  # noqa: S104 - bound inside the private compose network only
    skill_port: int = 8000
    skill_allowed_hosts: str = ""
    allow_eval_mutants: bool = False


class AgentSettings(_Base):
    gateway_url: str = "http://litellm:4000/v1"
    gateway_api_key: SecretStr
    gateway_timeout_s: float = 90.0
    orders_mcp_url: str = "http://mcp-orders:8000/mcp"
    billing_mcp_url: str = "http://mcp-billing:8000/mcp"
    service_mcp_url: str = "http://mcp-service:8000/mcp"


class GatewayInfoSettings(_Base):
    gateway_config_dir: str = "/app/gateway"
    eval_reports_dir: str = "/app/evals/reports"


@lru_cache
def database_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def identity_settings() -> IdentitySettings:
    return IdentitySettings()


@lru_cache
def skill_server_settings() -> SkillServerSettings:
    return SkillServerSettings()


@lru_cache
def agent_settings() -> AgentSettings:
    return AgentSettings()


@lru_cache
def gateway_info_settings() -> GatewayInfoSettings:
    return GatewayInfoSettings()
