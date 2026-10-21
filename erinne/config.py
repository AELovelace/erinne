"""Settings, read from ERINNE_* environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERINNE_", env_file=".env", extra="ignore")

    # Storage
    data_dir: Path = Path("data")

    # Web / sessions
    host: str = "127.0.0.1"
    port: int = 38200
    secret_key: str = ""
    session_max_age: int = 24 * 3600
    session_https_only: bool = False

    # Login throttling: lock a username+IP pair after this many failures.
    login_max_failures: int = 5
    login_lockout_seconds: int = 300

    # Usernames that are always admins (comma-separated); bootstrap only.
    admin_users: str = ""

    # Inference (llama-server)
    llm_url: str = "http://127.0.0.1:8081"
    embed_url: str = "http://127.0.0.1:8082"
    llm_timeout: float = 120.0
    llm_model: str = "qwen3-8b-q4_k_m"   # label recorded with each answer (the server decides)
    max_question_chars: int = 2000

    # Embeddings: vector size fixes the chunk_vec table's shape.
    # Qwen3-Embedding-0.6B won the 2026-10-20 bake-off (recall@5 0.96 vs 0.92
    # for jina-v2 and 0.91 for nomic-v1.5). It is instruction-aware: queries get
    # a task instruction, documents get none. Its llama-server needs --pooling last.
    embed_model: str = "qwen3-embedding-0.6b-q8_0"
    embed_dim: int = Field(default=1024, gt=0)
    embed_query_prefix: str = ("Instruct: Given a question from hotel staff, retrieve document passages "
                               "that answer it\nQuery: ")
    embed_doc_prefix: str = ""
    embed_batch_size: int = 32
    embed_timeout: float = 120.0

    # Uploads
    max_pdf_bytes: int = 100 * 1024 * 1024
    max_pdf_pages: int = 5000          # the OPERA Cloud manual alone is 3,300 pages
    max_md_bytes: int = 5 * 1024 * 1024

    # Ingestion
    ocr_command: str = "ocrmypdf"      # run when present on PATH; skipped otherwise
    ocr_min_chars: int = 25            # pages with less text than this count as scanned
    ocr_timeout: int = 1800
    chunk_target_tokens: int = 350
    chunk_max_tokens: int = 512
    chunk_overlap_tokens: int = 50
    job_max_attempts: int = 3
    job_stale_minutes: int = 30        # running jobs older than this are retried

    # Retrieval
    retrieval_k: int = 30              # candidates from each of keyword and vector search
    context_chunks: int = 6            # chunks handed to the model
    context_tokens: int = 3000         # ...within this budget
    fts_section_weight: float = 3.0    # bm25 weight of section_path vs body text
    # Not-found shortcut (answer without calling the model): the closest chunk is
    # farther than vector_max_distance, or there is no keyword match at all and it
    # is farther than vector_max_distance_no_keywords. Cosine distances depend on
    # the embedder; these fit Qwen3-Embedding (answerable questions: p95 0.35,
    # max 0.48). Re-tune with `erinne eval-retrieval --no-gate` after changing it.
    vector_max_distance: float = 0.50
    vector_max_distance_no_keywords: float = 0.42
    rerank_url: str = ""               # optional llama-server --reranking endpoint
    rerank_candidates: int = 20

    # Teacher (writes training and evaluation data). "llamacpp" = the local
    # llm_url; "openai" = a remote OpenAI-compatible API. A remote teacher only
    # ever sees document text from the libraries named in
    # teacher_allowed_libraries (comma-separated); empty = none at all.
    teacher_flavor: str = "llamacpp"
    teacher_url: str = "https://api.openai.com"
    # GPT-6 Luna: newest small tier, ~$0.10 / $0.50 per 1M tokens (Oct 2026);
    # check the exact model id your account lists (GET /v1/models).
    teacher_model: str = "gpt-6-luna"
    teacher_api_key: str = ""
    # USD per 1M tokens, for the spend tracker / --budget (GPT-6 Luna, Oct 2026).
    teacher_price_in: float = 0.10
    teacher_price_out: float = 0.50
    teacher_allowed_libraries: str = ""

    # Long-term memory: "auto" extracts facts users state about themselves;
    # "explicit" only saves on "remember that ..." requests; "off" disables it.
    memory_mode: str = "auto"
    memory_max_per_user: int = 50

    @property
    def db_path(self) -> Path:
        return self.data_dir / "erinne.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def tmp_dir(self) -> Path:
        # Inside data_dir so finished uploads move into blobs/ with a rename.
        return self.data_dir / "tmp"

    @property
    def admin_usernames(self) -> set[str]:
        return {u.strip().lower() for u in self.admin_users.split(",") if u.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
