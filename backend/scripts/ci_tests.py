"""Explicit offline suites, each in its existing isolated subprocess harness.

No pytest discovery, database fallback, .env loading or integration migrations.
"""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SUITES = (
    "database_safety", "private_images", "public_generation_errors", "embedding_recovery",
    "document_processing_reliability", "summary_concurrency", "resource_admission",
    "upload_resource_limits", "auth_hardening", "rag_correctness", "database_scalability",
    "observability",
)


def main():
    # Never inherit application credentials, provider keys or PG routing overrides.
    environment = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL",
    }}
    environment.update(ENVIRONMENT="test", DATABASE_URL="", TEST_DATABASE_URL="",
                       PYTHON_DOTENV_DISABLED="1", PYTHONUTF8="1")
    failed = []
    for suite in SUITES:
        result = subprocess.run([sys.executable, "-B", f"tests/test_{suite}.py"],
                                cwd=ROOT, env=environment, timeout=180, check=False)
        if result.returncode:
            failed.append(suite)
    if failed:
        raise SystemExit("Failed offline suites: " + ", ".join(failed))
    print(f"All {len(SUITES)} offline suites passed")


if __name__ == "__main__":
    main()
