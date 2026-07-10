"""S3-compatible object storage abstraction.

In Phase 1 we use boto3 against MinIO (local) or S3 (cloud) — same code path.
``LocalObjectStore`` is a filesystem-backed implementation used by the
single-image deployment (and tests): multipart parts are PUT to API-relative
URLs served by the ``uploads`` router, staged on disk, and assembled on
complete.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PresignedPart:
    part_number: int
    url: str


@dataclass(slots=True)
class PresignedMultipartUpload:
    upload_id: str
    parts: list[PresignedPart]
    part_size_bytes: int


class ObjectStore(Protocol):
    bucket: str

    def ensure_bucket(self) -> None: ...
    def presign_multipart(
        self, *, object_key: str, size_bytes: int, content_type: str | None = None
    ) -> PresignedMultipartUpload: ...
    def complete_multipart(
        self,
        *,
        object_key: str,
        upload_id: str,
        parts: list[tuple[int, str]],
    ) -> None: ...
    def upload_file(
        self, local_path: str, object_key: str, content_type: str | None = None
    ) -> None: ...
    def download_to_file(self, object_key: str, local_path: str) -> None: ...
    def head(self, object_key: str) -> dict | None: ...
    def delete(self, object_key: str) -> None: ...
    def presign_get(self, object_key: str, expires: int = 3600) -> str: ...


# ---------------------------------------------------------------------------
# S3 / MinIO implementation
# ---------------------------------------------------------------------------


class S3ObjectStore:
    bucket: str

    # 16 MiB part size: minimum for multipart on S3 is 5 MiB, picking 16 keeps
    # the number of presigned URLs reasonable even for 30 GB uploads (~1900 parts).
    PART_SIZE = 16 * 1024 * 1024

    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        public_endpoint_url: str | None = None,
    ):
        self.bucket = bucket
        self._endpoint = endpoint_url
        self._public_endpoint = public_endpoint_url or endpoint_url
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        # A second client whose presigned URLs point at the public endpoint
        # (so the browser can reach MinIO at localhost:9000 even though the API
        # container talks to it at minio:9000).
        self._presign_client = boto3.client(
            "s3",
            endpoint_url=self._public_endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchBucket", "NoSuchKey"} or "Not Found" in str(e):
                self._client.create_bucket(Bucket=self.bucket)
                log.info("created bucket %s", self.bucket)
            else:
                raise

    def presign_multipart(
        self,
        *,
        object_key: str,
        size_bytes: int,
        content_type: str | None = None,
    ) -> PresignedMultipartUpload:
        create_kwargs: dict = {"Bucket": self.bucket, "Key": object_key}
        if content_type:
            create_kwargs["ContentType"] = content_type
        resp = self._client.create_multipart_upload(**create_kwargs)
        upload_id = resp["UploadId"]
        part_size = self.PART_SIZE
        num_parts = max(1, (size_bytes + part_size - 1) // part_size)
        parts = [
            PresignedPart(
                part_number=i + 1,
                url=self._presign_client.generate_presigned_url(
                    "upload_part",
                    Params={
                        "Bucket": self.bucket,
                        "Key": object_key,
                        "UploadId": upload_id,
                        "PartNumber": i + 1,
                    },
                    ExpiresIn=3600,
                ),
            )
            for i in range(num_parts)
        ]
        return PresignedMultipartUpload(
            upload_id=upload_id, parts=parts, part_size_bytes=part_size
        )

    def complete_multipart(
        self,
        *,
        object_key: str,
        upload_id: str,
        parts: list[tuple[int, str]],
    ) -> None:
        self._client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": pn, "ETag": etag.strip('"')}
                    for pn, etag in sorted(parts, key=lambda x: x[0])
                ]
            },
        )

    def upload_file(
        self, local_path: str, object_key: str, content_type: str | None = None
    ) -> None:
        extra = {"ContentType": content_type} if content_type else None
        self._client.upload_file(local_path, self.bucket, object_key, ExtraArgs=extra)

    def download_to_file(self, object_key: str, local_path: str) -> None:
        self._client.download_file(self.bucket, object_key, local_path)

    def head(self, object_key: str) -> dict | None:
        try:
            return self._client.head_object(Bucket=self.bucket, Key=object_key)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey"}:
                return None
            raise

    def delete(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=object_key)

    def presign_get(self, object_key: str, expires: int = 3600) -> str:
        return self._presign_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires,
        )


# ---------------------------------------------------------------------------
# Local filesystem fallback (tests / minimal dev)
# ---------------------------------------------------------------------------


_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class LocalObjectStore:
    """Stores objects under ``root/bucket/key``.

    Multipart uploads are real: ``presign_multipart`` returns API-relative
    part URLs handled by the ``uploads`` router, which stages each part under
    ``root/.uploads/<upload_id>/``. ``complete_multipart`` assembles the parts
    into the final object. This keeps the browser upload flow identical to the
    S3 path while requiring zero external services (single-image deployment).
    """

    bucket: str

    # 64 MiB parts: a 30 GB upload is ~480 requests through the nginx proxy.
    PART_SIZE = 64 * 1024 * 1024

    def __init__(self, *, root: str, bucket: str):
        self.bucket = bucket
        self._root = os.path.abspath(root)
        os.makedirs(self._bucket_root(), exist_ok=True)

    def _bucket_root(self) -> str:
        return os.path.join(self._root, self.bucket)

    def _path_for(self, object_key: str) -> str:
        return os.path.join(self._bucket_root(), object_key)

    def _staging_dir(self, upload_id: str) -> str:
        if not _UPLOAD_ID_RE.match(upload_id):
            raise ValueError(f"invalid upload_id: {upload_id!r}")
        return os.path.join(self._root, ".uploads", upload_id)

    def part_path(self, upload_id: str, part_number: int) -> str:
        """Staging path for one uploaded part. Validates inputs (used by the
        uploads router with untrusted path parameters)."""
        if not 1 <= part_number <= 10_000:
            raise ValueError(f"invalid part_number: {part_number}")
        d = self._staging_dir(upload_id)
        if not os.path.isdir(d):
            raise FileNotFoundError(f"unknown upload_id: {upload_id}")
        return os.path.join(d, f"part_{part_number:05d}")

    def ensure_bucket(self) -> None:
        os.makedirs(self._bucket_root(), exist_ok=True)

    def presign_multipart(
        self,
        *,
        object_key: str,
        size_bytes: int,
        content_type: str | None = None,
    ) -> PresignedMultipartUpload:
        upload_id = uuid.uuid4().hex
        os.makedirs(self._staging_dir(upload_id), exist_ok=True)
        part_size = self.PART_SIZE
        num_parts = max(1, (size_bytes + part_size - 1) // part_size)
        # URLs are relative to the API base; the frontend resolves them
        # (absolute S3 URLs pass through unchanged).
        parts = [
            PresignedPart(
                part_number=i + 1,
                url=f"/uploads/{upload_id}/{i + 1}",
            )
            for i in range(num_parts)
        ]
        return PresignedMultipartUpload(
            upload_id=upload_id, parts=parts, part_size_bytes=part_size
        )

    def complete_multipart(
        self,
        *,
        object_key: str,
        upload_id: str,
        parts: list[tuple[int, str]],
    ) -> None:
        staging = self._staging_dir(upload_id)
        dest = self._path_for(object_key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        ordered = sorted(parts, key=lambda x: x[0])
        try:
            with open(dest, "wb") as out:
                for part_number, _etag in ordered:
                    src = os.path.join(staging, f"part_{part_number:05d}")
                    if not os.path.exists(src):
                        raise FileNotFoundError(
                            f"part {part_number} missing for upload {upload_id}"
                        )
                    with open(src, "rb") as fh:
                        shutil.copyfileobj(fh, out, length=8 * 1024 * 1024)
        except Exception:
            if os.path.exists(dest):
                os.remove(dest)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def upload_file(
        self, local_path: str, object_key: str, content_type: str | None = None
    ) -> None:
        dest = self._path_for(object_key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(local_path, dest)

    def download_to_file(self, object_key: str, local_path: str) -> None:
        shutil.copyfile(self._path_for(object_key), local_path)

    def head(self, object_key: str) -> dict | None:
        p = self._path_for(object_key)
        if not os.path.exists(p):
            return None
        return {"ContentLength": os.path.getsize(p)}

    def delete(self, object_key: str) -> None:
        p = self._path_for(object_key)
        if os.path.exists(p):
            os.remove(p)

    def presign_get(self, object_key: str, expires: int = 3600) -> str:
        return f"file://{self._path_for(object_key)}"


# ---------------------------------------------------------------------------
# Factory + key helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_object_store() -> ObjectStore:
    settings = get_settings()
    if settings.object_store_kind == "local":
        store = LocalObjectStore(root=settings.local_store_root, bucket=settings.s3_bucket)
    else:
        store = S3ObjectStore(
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            public_endpoint_url=settings.s3_public_endpoint_url,
        )
    return store


def reset_object_store_cache() -> None:
    get_object_store.cache_clear()


def dataset_raw_key(workspace_id: str, dataset_id: str, filename: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[:200]
    return f"workspaces/{workspace_id}/datasets/{dataset_id}/raw/{safe}"


def dataset_parquet_key(workspace_id: str, dataset_id: str) -> str:
    return f"workspaces/{workspace_id}/datasets/{dataset_id}/parquet/data.parquet"


def step_checkpoint_key(workspace_id: str, job_id: str, node_id: str) -> str:
    h = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:12]
    return f"workspaces/{workspace_id}/jobs/{job_id}/steps/{node_id}_{h}.parquet"


def parse_file_url(url: str) -> str:
    """Extract the local path from a file:// URL (used by LocalObjectStore)."""
    parsed = urlparse(url)
    return parsed.path
