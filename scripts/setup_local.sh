#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root_dir/backend/.env"
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  python_bin="$VIRTUAL_ENV/bin/python"
else
  python_bin=""
  for candidate in python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then python_bin="$(command -v "$candidate")"; break; fi
  done
  if [[ -z "$python_bin" ]]; then
    echo "Python 3.11+ is required. Install Python 3.11 or newer and retry." >&2
    exit 2
  fi
  "$python_bin" -m venv "$root_dir/backend/.venv"
  python_bin="$root_dir/backend/.venv/bin/python"
fi
if [[ -e "$env_file" ]]; then
  echo "Keeping existing backend/.env"
else
  jwt_secret="$("$python_bin" -c 'import secrets; print(secrets.token_urlsafe(48))')"
  cat > "$env_file" <<EOF
APP_ENV=development
DATABASE_URL=postgresql+psycopg://pharmacy:change_me@localhost:5432/pharmacy
JWT_SECRET=$jwt_secret
ACCESS_TOKEN_MINUTES=10
REFRESH_TOKEN_DAYS=14
INVITATION_HOURS=48
CORS_ORIGINS=http://localhost:5173
RATE_LIMIT_STORAGE_URI=memory://
DARAJA_ENVIRONMENT=sandbox
PLATFORM_BACKUP_DIR=backups/tenant-exports
EOF
  chmod 600 "$env_file"
  echo "Created backend/.env with a generated development JWT secret."
fi

"$python_bin" - <<'PY'
from pathlib import Path
import sys
version = sys.version_info
if version < (3, 11):
    raise SystemExit(f"Python 3.11+ is required; selected interpreter is {version.major}.{version.minor}. Run this after activating backend/.venv.")
PY

cd "$root_dir"
"$python_bin" -m pip install -r backend/requirements.txt
echo "Dependencies installed. Configure a local PostgreSQL role/database, then start the app as documented in README.md."
