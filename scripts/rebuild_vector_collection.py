"""Rebuild the Milvus vector collection and reindex documents."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drop/recreate the Milvus collection and reindex documents."
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation because this deletes existing vector data.",
    )
    parser.add_argument(
        "--directory",
        default=None,
        help="Directory to reindex. Defaults to the application's upload directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("rebuilding deletes existing vector data; pass --yes to confirm")

    from app.services.vector_index_service import vector_index_service

    result = vector_index_service.rebuild_collection_and_reindex(
        directory_path=args.directory,
        confirm=True,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
