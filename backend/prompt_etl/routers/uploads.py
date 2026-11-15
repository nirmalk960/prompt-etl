"""Direct upload endpoints for the local (filesystem) object store.

When ``OBJECT_STORE_KIND=local`` (single-image deployment), ``/datasets/init``
returns part URLs pointing here instead of S3 presigned URLs. The browser PUTs
each chunk; we stream it to the staging directory and return an ETag, exactly
mirroring the S3 multipart contract so the frontend needs no changes.

Like S3 presigned URLs, these endpoints are not behind auth headers: the
random 128-bit ``upload_id`` (created server-side by ``/datasets/init``) is
the capability token.
"""

from __future__ import annotations

import hashlib
import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response

from ..services.object_store import LocalObjectStore, get_object_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.put("/{upload_id}/{part_number}")
async def put_upload_part(
    upload_id: str,
    part_number: int,
    request: Request,
) -> Response:
    store = get_object_store()
    if not isinstance(store, LocalObjectStore):
        raise HTTPException(
            status_code=404,
            detail="direct upload is only available with the local object store",
        )
    try:
        path = store.part_path(upload_id, part_number)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    md5 = hashlib.md5()
    size = 0
    limit = store.PART_SIZE
    try:
        with open(path, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    fh.close()
                    os.remove(path)
                    raise HTTPException(status_code=413, detail="upload part exceeds 64 MiB")
                fh.write(chunk)
                md5.update(chunk)
    except HTTPException:
        raise
    except Exception:
        log.exception("part upload failed upload_id=%s part=%s", upload_id, part_number)
        raise HTTPException(status_code=500, detail="part write failed") from None

    etag = md5.hexdigest()
    return Response(
        status_code=200,
        headers={"ETag": f'"{etag}"', "X-Part-Size": str(size)},
    )
