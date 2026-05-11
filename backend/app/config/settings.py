import os
from dotenv import load_dotenv

load_dotenv()


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
    RUNS_DIR: str = optional_env("RUNS_DIR", "/tmp/agent-runs")

    # limits
    MAX_WORKSPACE_FILES: int = optional_int("MAX_WORKSPACE_FILES", 200)
    MAX_OPERATIONS: int = optional_int("MAX_OPERATIONS", 20)
    MAX_DELETES: int = optional_int("MAX_DELETES", 3)

    # git / execution
    GIT_DIFF_CONTEXT: int = optional_int("GIT_DIFF_CONTEXT", 3)


settings = Settings()