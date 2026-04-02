from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    s3_bucket_name: str
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_endpoint_url: str = "https://s3.amazonaws.com"
    s3_create_bucket_if_missing: bool = False
    auth_code_map: str
    cors_allowed_origins: str = "http://localhost:3000"
    cors_allow_origin_regex: str | None = r"https://.*\.vercel\.app"

    @model_validator(mode="after")
    def validate_s3_credentials(self) -> "Settings":
        if bool(self.s3_access_key) != bool(self.s3_secret_key):
            raise ValueError("S3_ACCESS_KEY and S3_SECRET_KEY must be set together")
        return self

    def _parse_entries(self) -> list[tuple[str, str, str]]:
        entries = []
        for entry in self.auth_code_map.split(","):
            entry = entry.strip()
            parts = entry.split(":", maxsplit=2)
            if len(parts) == 3:
                entries.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
        return entries

    @property
    def auth_code_to_app(self) -> dict[str, str]:
        return {code: name for code, name, _ in self._parse_entries()}

    @property
    def auth_code_to_url(self) -> dict[str, str]:
        return {code: url for code, _, url in self._parse_entries()}

    @property
    def use_static_s3_credentials(self) -> bool:
        return bool(self.s3_access_key and self.s3_secret_key)

    @property
    def parsed_cors_allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def parsed_cors_allow_origin_regex(self) -> str | None:
        if self.cors_allow_origin_regex is None:
            return None
        value = self.cors_allow_origin_regex.strip()
        return value or None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
