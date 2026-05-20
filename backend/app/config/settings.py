import os


# -----------------------------
# helpers
# -----------------------------
def required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def optional_env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value.strip() != "" else default


def optional_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


# -----------------------------
# settings
# -----------------------------
class Settings:
    # core paths
    REPO_ROOT: str = required_env("REPO_ROOT")
    RUNS_DIR: str = optional_env("RUNS_DIR", "/opt/agent-repos/worktrees")
    ARTIFACTS_DIR: str = optional_env("ARTIFACTS_DIR", "/opt/agent-repos/artifacts")
    SEMANTIC_DIR: str = optional_env("SEMANTIC_DIR", "/app/backend/app/semantic")

    # limits
    MAX_WORKSPACE_FILES: int = optional_int("MAX_WORKSPACE_FILES", 200)
    MAX_OPERATIONS: int = optional_int("MAX_OPERATIONS", 20)
    MAX_DELETES: int = optional_int("MAX_DELETES", 3)

    # git / execution
    GIT_DIFF_CONTEXT: int = optional_int("GIT_DIFF_CONTEXT", 3)

    # LLM config
    LLM_BASE_URL: str = optional_env("LLM_BASE_URL", "http://localhost:7000")
    LLM_MODEL: str = optional_env("LLM_MODEL", "Qwen/Qwen2.5-Coder-3B-Instruct")
    LLM_API_KEY: str = optional_env("LLM_API_KEY", "")

    # Embedding config
    EMBEDDING_ENABLED: bool = optional_env("EMBEDDING_ENABLED", "false").lower() in ("true", "1", "yes")
    EMBEDDING_BASE_URL: str = optional_env("EMBEDDING_BASE_URL", "")
    EMBEDDING_MODEL: str = optional_env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    EMBEDDING_TOP_K: int = optional_int("EMBEDDING_TOP_K", 3)
    EMBEDDING_THRESHOLD: float = float(optional_env("EMBEDDING_THRESHOLD", "0.3"))
    EMBEDDING_TIMEOUT: int = optional_int("EMBEDDING_TIMEOUT", 15)

    @property
    def resolved_embedding_base_url(self) -> str:
        return self.EMBEDDING_BASE_URL or self.LLM_BASE_URL


settings = Settings()