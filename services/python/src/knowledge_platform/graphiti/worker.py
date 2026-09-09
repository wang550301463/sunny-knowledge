"""Graphiti projection worker — NOT YET RECOVERED (placeholder).

The original worker (durable knowledge-outbox consumer with ACL
reconciliation) was lost in the 2026-09-09 disk wipe. Stdout reassembly
produced a file whose middle section actually belongs to retrieval/worker.py
(it references retrieval embedding configuration), so it cannot be trusted.
This placeholder fails fast instead of running wrong code. Recovery options:
re-run the stdout reassembler with per-file fragment ownership checks, or
rewrite from the GraphService/Catalog contract once the service routes are
restored. See .workbuddy/memory/2026-09-09.md for the reconstruction log.
"""
import sys


def main() -> int:
    print("graphiti worker: not recovered yet; see module docstring", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
