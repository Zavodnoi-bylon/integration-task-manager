"""Explicit local ingestion/review CLI. No collector or model is invoked."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from activity.ingest import (ImportValidationError, StorageError, ingest_bundle,
                             normalize_file, read_json_file, writer_connection)


def write_bundle(path, bundle):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                         prefix=".normalize-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(bundle, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize", help="DB/network-free Telegram normalization")
    normalize.add_argument("input")
    normalize.add_argument("--output", required=True)
    normalize.add_argument("--timezone", help="Explicit fallback timezone if Unix timestamps are absent")
    messages = commands.add_parser("ingest-messages", help="Import immutable message versions")
    messages.add_argument("input")
    messages.add_argument("--timezone")
    messages.add_argument("--dry-run", action="store_true", help="Normalize/count only; no database")
    proposals = commands.add_parser("ingest-proposals", help="Import evidence-backed candidates, without approval")
    proposals.add_argument("input")
    proposals.add_argument("--dry-run", action="store_true")
    proposals.add_argument("--messages", help="Normalized bundle for all evidence checks during offline --dry-run")
    proposals.add_argument("--allow-demo", action="store_true", help="Explicitly permit fictional fixtures in a test database")
    review = commands.add_parser("review", help="Explicit audited human decision")
    review.add_argument("proposal_id")
    review.add_argument("--decision", choices=("accept", "reject"), required=True)
    review.add_argument("--actor", required=True)
    review.add_argument("--reason")
    review.add_argument("--expected-revision", type=int)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"normalize", "ingest-messages"}:
            bundle = normalize_file(args.input, fallback_timezone=args.timezone)
            if args.command == "normalize":
                if Path(args.input).resolve() == Path(args.output).resolve():
                    raise ImportValidationError("output_must_not_overwrite_input")
                write_bundle(args.output, bundle)
                result = {"status": "normalized", **bundle["counts"], "sources": len(bundle["sources"])}
            elif args.dry_run:
                result = {"status": "dry_run", **bundle["counts"], "sources": len(bundle["sources"]),
                          "database_checked": False}
            else:
                with writer_connection() as conn:
                    result = ingest_bundle(conn, bundle)
        elif args.command == "ingest-proposals":
            from activity.proposals import (evidence_index, ingest_proposals,
                                            validate_document, verify_offline_evidence)
            document, digest = read_json_file(args.input)
            document = validate_document(document)
            if args.dry_run:
                if not args.messages:
                    result = {"status": "syntax_only", "proposals": len(document["proposals"]),
                              "evidence_verified": False, "database_checked": False,
                              "note": "Provide --messages BUNDLE.json for full offline evidence validation; nothing was imported"}
                else:
                    bundle, _ = read_json_file(args.messages)
                    verify_offline_evidence(document, evidence_index(bundle))
                    result = {"status": "dry_run", "proposals": len(document["proposals"]),
                              "evidence_verified": True, "database_checked": False}
            else:
                if args.messages:
                    raise ImportValidationError("--messages is only used with --dry-run; database imports verify database evidence")
                with writer_connection() as conn:
                    result = ingest_proposals(conn, document, content_hash=digest,
                                              source_name=Path(args.input).name, allow_demo=args.allow_demo)
        else:
            from activity.proposals import review_proposal
            with writer_connection() as conn:
                result = review_proposal(conn, args.proposal_id, decision=args.decision,
                                         actor=args.actor, reason=args.reason,
                                         expected_revision=args.expected_revision)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ImportValidationError, StorageError) as error:
        print(json.dumps({"status": "error", "code": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "error", "code": "input_or_output_error"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
