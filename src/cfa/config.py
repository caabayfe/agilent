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


class MigrationSettings(_Base):
    """Only the one-shot migrate job gets the database owner's credentials."""

    migrate_database_url: SecretStr


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
    disabled_skills: str = ""  # kill switch, comma-separated skill names

    @property
    def disabled(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.disabled_skills.split(",") if s.strip())


class SkillDatabaseSettings(_Base):
    """One DSN per skill: each skill logs in as its own least-privilege role."""

    database_url_orders: SecretStr
    database_url_billing: SecretStr
    database_url_service: SecretStr

    def for_skill(self, skill: str) -> str:
        dsn: SecretStr = getattr(self, f"database_url_{skill}")
        return dsn.get_secret_value()


class AgentSettings(_Base):
    gateway_url: str = "http://litellm:4000/v1"
    gateway_api_key: SecretStr
    gateway_timeout_s: float = 90.0
    skill_server_url: str = "http://mcp-customer:8000/mcp"
    # MCP client resilience (see cfa.agent.resilience)
    skill_attempt_timeout_s: float = 5.0
    skill_retry_attempts: int = 3
    skill_breaker_threshold: int = 3
    skill_breaker_reset_s: float = 15.0


class GatewayInfoSettings(_Base):
    gateway_config_dir: str = "/app/gateway"
    eval_reports_dir: str = "/app/evals/reports"
    # UI model selector (cfa.gateway_admin). Off unless the local stack turns it on.
    gateway_admin_url: str = "http://litellm:4001"
    gateway_admin_enabled: bool = False


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
def skill_database_settings() -> SkillDatabaseSettings:
    return SkillDatabaseSettings()


@lru_cache
def agent_settings() -> AgentSettings:
    return AgentSettings()


@lru_cache
def gateway_info_settings() -> GatewayInfoSettings:
    return GatewayInfoSettings()
