"""Command-line entry point.

Usage:
    python -m repopilot.cli <repo_url> "<question>" [--inject-fake] [--no-cache] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys

from . import pipeline
from .pipeline import Result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="repopilot",
        description="Ask a question about a public GitHub repository, with verified citations.",
    )
    parser.add_argument("repo_url", help="Public GitHub URL, e.g. https://github.com/owner/repo")
    parser.add_argument("question", help="Question to ask about the repository")
    parser.add_argument(
        "--inject-fake",
        action="store_true",
        help="Demo: add a fabricated citation and show the verifier catching it.",
    )
    parser.add_argument("--no-cache", action="store_true", help="Ignore any cached result.")
    parser.add_argument("--json", action="store_true", help="Print the raw result as JSON.")
    args = parser.parse_args(argv)

    result = pipeline.answer(
        args.repo_url,
        args.question,
        inject_fake=args.inject_fake,
        use_cache=not args.no_cache,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _print_result(result)
    return 0 if result.ok else 1


def _print_result(result: Result) -> None:
    print(f"Repository: {result.repo_url}")
    print(f"Question: {result.question}")
    print(f"Intent: {result.intent or 'unknown'}")

    if result.error:
        print(f"\nCould not answer: {result.error}")
        return

    if result.cached:
        print("(cached result)")

    print("\nAnswer:")
    if not result.claims:
        print("  (no claims survived verification)")
    for claim in result.claims:
        label = claim.get("label", "unknown")
        statement = claim.get("statement", "")
        citations = ", ".join(claim.get("citations") or []) or "no citation"
        print(f"  - [{label}] {statement}  ({citations})")
        if claim.get("quote"):
            print(f"      quote: {claim['quote']!r}")
        if claim.get("status") == "flagged":
            print(f"      warning: {claim.get('reason')}")

    if result.missing:
        print("\nNot found: " + ", ".join(result.missing))

    if result.notes:
        print("\nNotes: " + "; ".join(result.notes))

    if result.dropped:
        print("\nDropped (failed verification):")
        for claim in result.dropped:
            print(f"  - {claim.get('statement', '')}")
            print(f"      reason: {claim.get('reason')}")

    if result.cannot_confirm:
        print("\nCannot confirm from code:")
        for line in result.cannot_confirm:
            print(f"  - {line}")

    print(f"\nVerifier: {result.badge}")


if __name__ == "__main__":
    sys.exit(main())
