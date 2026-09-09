#!/usr/bin/env python3
"""Initialize the configured raw bucket and verify Temporal before worker acceptance."""

import asyncio
import time

from botocore.exceptions import ClientError
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client

from knowledge_platform.ingest.config import IngestSettings
from knowledge_platform.ingest.storage import S3Store


def prepare_bucket(store):
    try:
        store.client.head_bucket(Bucket=store.bucket)
    except ClientError as error:
        if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise
        try:
            store.client.create_bucket(Bucket=store.bucket)
        except ClientError as race:
            if race.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                raise


async def main():
    settings = IngestSettings()
    store = S3Store.from_settings(settings)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            async with asyncio.timeout(15):
                await asyncio.to_thread(prepare_bucket, store)
                client = await Client.connect(settings.ingest_temporal_address)
                await client.workflow_service.describe_namespace(
                    DescribeNamespaceRequest(namespace=settings.ingest_temporal_namespace)
                )
            print("Raw bucket and Temporal namespace are ready; existing data retained.")
            return 0
        except Exception:
            # Deployment failure details can contain endpoints/credentials; do not echo them.
            await asyncio.sleep(2)
    print("Raw storage or Temporal did not become ready within 120 seconds.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
