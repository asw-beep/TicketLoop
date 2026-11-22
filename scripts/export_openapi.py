"""Write the API's OpenAPI description to a file, for the frontend's generated TypeScript types.

    uv run python -m scripts.export_openapi frontend/openapi.json

`npm run api:types` in frontend/ runs this, then openapi-typescript. CI runs both and fails
if the committed files differ, so the frontend cannot drift from the backend unnoticed.
"""

import argparse
import json
from pathlib import Path

from app.core.config import Settings
from app.main import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="where to write the JSON")
    args = parser.parse_args(argv)

    # Settings without .env: the description must not depend on whose laptop runs this.
    # Building the app opens no database connection; the engine connects on first use.
    spec = create_app(Settings(_env_file=None, environment="test")).openapi()
    text = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    args.target.write_bytes(text.encode())  # bytes: LF line endings on Windows too


if __name__ == "__main__":
    main()
