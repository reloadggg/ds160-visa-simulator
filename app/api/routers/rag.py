from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.simple_auth import get_current_admin_session
from app.core.settings import settings
from app.db.session import get_db
from app.services.visa_policy_ingest_service import (
    PolicyKnowledgeParseError,
    PolicyKnowledgeIngestService,
    PolicyKnowledgeUploadTooLargeError,
    UnsupportedPolicyKnowledgeFileError,
)


router = APIRouter(prefix="/v1/rag", tags=["rag"])
RAG_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


def _require_admin(request: Request, db: Session = Depends(get_db)) -> None:
    if get_current_admin_session(request, db, touch=False) is None:
        raise HTTPException(status_code=403, detail="RAG 管理仅对后台开放。")


@router.get("/status")
def get_rag_status(_: None = Depends(_require_admin)) -> dict:
    return PolicyKnowledgeIngestService().status_payload()


@router.post("/files", status_code=202)
async def upload_rag_file(
    _: None = Depends(_require_admin),
    file: UploadFile = File(),
    title: str | None = Form(default=None),
    url: str | None = Form(default=None),
    visa_family: str | None = Form(default=None),
    country: str | None = Form(default=None),
    post: str | None = Form(default=None),
    section_path: str | None = Form(default=None),
) -> dict:
    try:
        raw_bytes = await _read_upload_with_limit(file)
        result = PolicyKnowledgeIngestService().ingest_upload(
            filename=file.filename or "policy-source",
            raw_bytes=raw_bytes,
            source_type="third_party_reference",
            title=title,
            url=url,
            visa_family=visa_family,
            country=country,
            post=post,
            section_path=section_path,
        )
    except PolicyKnowledgeUploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except PolicyKnowledgeParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedPolicyKnowledgeFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="RAG 知识库索引失败，请检查向量库和嵌入/重排模型配置。",
        ) from exc

    if result.skipped:
        return result.model_dump(mode="json")
    return result.model_dump(mode="json")


async def _read_upload_with_limit(file: UploadFile) -> bytes:
    max_bytes = settings.rag_upload_max_size_mb * 1024 * 1024
    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(RAG_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > max_bytes:
            raise PolicyKnowledgeUploadTooLargeError(
                f"Uploaded policy file exceeds {settings.rag_upload_max_size_mb}MB limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)
