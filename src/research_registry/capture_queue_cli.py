from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .capture_queue import CaptureQueue, QueuedCaptureBundle
from .config import load_settings
from .service import RegistryService


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and flush the implicit research capture queue.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List queued capture bundles.")
    subparsers.add_parser("flush", help="Flush queued bundles into the registry.")

    enqueue_parser = subparsers.add_parser("enqueue", help="Enqueue a capture bundle from a JSON file or stdin.")
    enqueue_parser.add_argument("--file", help="Path to a JSON file containing one capture bundle.")
    enqueue_parser.add_argument("--stdin", action="store_true", help="Read a capture bundle from stdin.")

    args = parser.parse_args()
    settings = load_settings()
    queue = CaptureQueue(settings.capture_queue_path)

    if args.command == "list":
        pending = queue.list_pending()
        for bundle in pending:
            print(f"{bundle.queue_id} | retries={bundle.retry_count} | topic={bundle.normalized_topic} | prompt={bundle.prompt}")
        if not pending:
            print("no pending captures")
        return

    if args.command == "flush":
        service = RegistryService(settings.db_path)
        service.initialize()
        result = queue.flush(service)
        print(f"flushed={len(result.flushed_queue_ids)} failed={len(result.failed_queue_ids)}")
        for report_id in result.stored_report_ids:
            print(f"report={report_id}")
        return

    if args.command == "enqueue":
        if bool(args.file) == bool(args.stdin):
            parser.error("choose exactly one of --file or --stdin")
        raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        bundle = QueuedCaptureBundle.model_validate_json(raw)
        queue.enqueue(bundle)
        print(bundle.queue_id)
        return
