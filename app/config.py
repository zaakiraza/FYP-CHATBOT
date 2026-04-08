from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    mongodb_uri: str = Field(default="mongodb://localhost:27017", validation_alias="MONGODB_URI")
    mongodb_db_name: str = Field(default="therakonnect", validation_alias="MONGODB_DB_NAME")

    soap_chunks_collection: str = Field(default="soap_chunks", validation_alias="SOAP_CHUNKS_COLLECTION")
    soap_source_collection: str | None = Field(default=None, validation_alias="SOAP_SOURCE_COLLECTION")

    jwt_secret: str = Field(default="", validation_alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_issuer: str | None = Field(default=None, validation_alias="JWT_ISSUER")
    jwt_audience: str | None = Field(default=None, validation_alias="JWT_AUDIENCE")

    # Local testing only: skip Bearer JWT and use this therapist id for DB filters (never enable in production)
    skip_therapist_jwt: bool = Field(default=False, validation_alias="SKIP_THERAPIST_JWT")
    dev_therapist_id: str = Field(default="", validation_alias="DEV_THERAPIST_ID")

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")

    gemini_chat_model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_CHAT_MODEL")
    # Use a model that supports embed_content on the Gemini API (see Google AI embeddings docs).
    embedding_model: str = Field(default="gemini-embedding-001", validation_alias="EMBEDDING_MODEL")

    rag_top_k: int = Field(default=5, validation_alias="RAG_TOP_K")
    rag_max_chunks_fetch: int = Field(default=8000, validation_alias="RAG_MAX_CHUNKS_FETCH")
    # If true, retrieval filters MongoDB by the authenticated therapist; if false (default), search all SOAP chunks.
    rag_therapist_scoped: bool = Field(default=False, validation_alias="RAG_THERAPIST_SCOPED")

    cors_origins: str = Field(
        default="https://therakonnect.com,http://localhost:3000",
        validation_alias="CORS_ORIGINS",
    )

    rate_limit_per_minute: int = Field(default=30, validation_alias="RATE_LIMIT_PER_MINUTE")

    api_host: str = Field(default="0.0.0.0", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")

    def effective_gemini_key(self) -> str:
        return (self.gemini_api_key or self.google_api_key or "").strip()

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
