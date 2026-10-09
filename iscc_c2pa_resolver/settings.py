"""Deployment settings, read from environment variables prefixed with `ISCC_C2PA_RESOLVER_`."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Resolver configuration. Defaults target the ISCC mainnet aggregator."""

    model_config = SettingsConfigDict(env_prefix="ISCC_C2PA_RESOLVER_", env_file=".env", extra="ignore")

    search_url: str = Field("https://search.iscc.io", description="Base URL of the iscc-search aggregator")
    search_index: str = Field("idp", description="Aggregator index name: idp (mainnet) or idptest (testnet)")
    search_timeout: float = Field(5.0, gt=0, description="Timeout in seconds for one aggregator request")
    gateway_timeout: float = Field(3.0, gt=0, description="Total time budget in seconds for one gateway URL probe")
    gateway_concurrency: int = Field(16, ge=1, description="Maximum parallel gateway and manifest fetches per process")
    manifest_timeout: float = Field(10.0, gt=0, description="Total time budget in seconds for one manifest fetch")
