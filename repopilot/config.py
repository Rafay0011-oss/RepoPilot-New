"""Central configuration for RepoPilot.

All tunable constants live here: the local model, prototype limits, ignore
lists, dependency/config filenames, scaling tags, and safety constants.
There are no secrets and no hardcoded absolute paths.
"""

from __future__ import annotations

# --- Local model (Ollama only; no cloud or proprietary AI) -------------------
MODEL_NAME: str = "qwen2.5-coder:3b"
OPTIONAL_MODEL_NAME: str = "qwen2.5-coder:7b"
OLLAMA_HOST: str = "http://localhost:11434"
TEMPERATURE: float = 0.0

# --- Prototype limits --------------------------------------------------------
MAX_EVIDENCE: int = 6              # snippets sent to the model per call
MAX_EVIDENCE_PER_TAG: int = 5      # catalog hits kept per scaling tag
MAX_SNIPPET_CHARS: int = 800       # per-snippet cap sent to the model
LLM_JSON_ATTEMPTS: int = 2         # model calls before failing (1 retry)
MAX_SOURCE_FILES: int = 400        # repos larger than this raise RepoTooLarge
MAX_FILE_BYTES: int = 200 * 1024   # files larger than ~200 KB are ignored
CLONE_DEPTH: int = 200             # shallow clone depth

# Bounded history deepening for "why" questions (see history.py). Used only on
# an already-cloned repo; if history stays truncated the answer is
# "not_documented" rather than a guessed commit.
HISTORY_DEEPEN_STEP: int = 200     # commits added per deepen attempt
HISTORY_MAX_DEPTH: int = 5000      # absolute cap on deepening
GIT_TIMEOUT_SECONDS: int = 120     # timeout for a single git subprocess

# --- Ignore lists ------------------------------------------------------------
# Directories skipped entirely during inventory.
IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        "node_modules", "vendor", "dist", "build",
        "__pycache__", ".venv", "venv", "env",
        ".mypy_cache", ".pytest_cache", ".ruff_cache",
    }
)

# Extensions treated as binary/non-source and skipped by the inventory.
BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
        ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mov", ".avi", ".mkv",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
        ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".o", ".a",
        ".pyc", ".pyo", ".wasm", ".jar",
        ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".db", ".sqlite", ".sqlite3", ".lock",
    }
)

# Extensions counted as "source files" for the MAX_SOURCE_FILES limit.
SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue",
        ".svelte", ".java", ".kt", ".scala", ".go", ".rs", ".rb", ".php",
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs", ".swift",
        ".m", ".mm", ".dart", ".lua", ".pl", ".r", ".jl", ".clj", ".ex",
        ".exs", ".erl", ".hs", ".ml", ".sh", ".ps1", ".sql", ".html", ".css",
        ".scss", ".less",
    }
)

# Path fragments that de-prioritize evidence (tests, docs, examples).
DOWNRANK_PATH_PARTS: tuple[str, ...] = (
    "test", "tests", "example", "examples", "docs", "doc",
)

# --- Dependency / config files (catalog.py) ---------------------------------
DEPENDENCY_FILES: tuple[str, ...] = (
    "requirements.txt", "pyproject.toml", "package.json",
    "Gemfile", "go.mod", "pom.xml",
)

CONFIG_FILE_NAMES: tuple[str, ...] = (
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "nginx.conf",
)

CONFIG_FILE_GLOBS: tuple[str, ...] = (
    "*.yaml", "*.yml", "docker-compose*.yml", "docker-compose*.yaml",
    "**/*.yaml", "**/*.yml",
)

# --- Scaling evidence tags (catalog.py) -------------------------------------
SCALING_TAGS: tuple[str, ...] = (
    "caching",
    "queue_background",
    "db_scaling",
    "horizontal_scaling",
    "stateless_auth",
    "rate_limiting",
    "async_io",
)

# --- GitHub URL validation (loader.py) --------------------------------------
# Accept only https://github.com/<owner>/<repo>, with an optional trailing
# slash and an optional ".git" suffix. Everything else is rejected.
GITHUB_URL_PATTERN: str = (
    r"^https://github\.com/"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})?"
    r"/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}?"
    r"(?:\.git)?/?$"
)

# --- Cache / output ----------------------------------------------------------
CACHE_DIR: str = "cache"
