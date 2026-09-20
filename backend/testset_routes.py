"""세트 준비 라우트 — 문서를 놓고, 사실 후보를 뽑고, 원천 하나를 세트 넷으로 펼친다.

공개본에도 싣는다. 세트를 만드는 **길**은 세트 자체와 다르다 — 이 프로젝트가 쓴 문항·정답은 올리지 않지만,
받은 사람이 제 문서로 제 세트를 만드는 자리는 있어야 한다. 그러지 않으면 공개본으로는 샘플 세트 너머를 잴 수 없다.

라우트는 세트 폴더와 그 옆의 원천 파일만 건드린다 — 둘 다 저장소 밖이라 여기서 만든 것이 올라가지 않는다.
개발용 라우트(프롬프트 실험·지표 다시 재기 등)는 `dev_routes.py`에 그대로 둔다.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import quality_testsets
import testset_derive
import testset_documents
import testset_facts
import testset_ready


class UploadDocument(BaseModel):
    """문서 하나 — 경로가 아니라 이름과 글이다. 문서는 UTF-8 텍스트라 본문으로 받는다."""

    name: str
    text: str


class UploadDocumentsRequest(BaseModel):
    edition: str
    documents: list[UploadDocument]


class SkeletonRequest(BaseModel):
    """고른 사실 후보의 id와, 인젝션 판을 가를 canary."""

    picked: list[str]
    canary: str = ""


class SourceRequest(BaseModel):
    source: dict


class PlaceDocumentsRequest(BaseModel):
    """받은 `.md`가 들어 있는 폴더와, 인젝션 판을 가를 canary. `write`가 거짓이면 자리만 정한다."""

    incoming: str
    canary: str | None = None
    write: bool = False


class DeriveRequest(BaseModel):
    """원천 하나(주제·문서·핵심 사실)와, 쓸지 여부. `write`가 거짓이면 내용만 돌려주고 파일을 만들지 않는다."""

    source: dict
    write: bool = False
    keep_closed_qa: bool = True


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/testsets/documents/upload")
    def upload_testset_documents(req: UploadDocumentsRequest) -> dict:
        """문서를 고른 묶음에 놓는다 — `문서`·`인젝션 판`은 두 자리(짧은·긴)에 같은 글을 둔다.
        길이를 나눠 재려면 판 이름(`긴 판` 등)을 그대로 보내면 그 자리에만 놓는다."""
        saved = []
        for doc in req.documents:
            try:
                saved.append(testset_documents.save_group(req.edition, doc.name, doc.text))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"saved": saved, **testset_documents.listing()}

    @router.delete("/api/testsets/documents/{name}")
    def remove_testset_document(name: str) -> dict:
        """문서 하나를 모든 자리에서 지운다 — 한 자리만 남으면 실행에서 그 항목이 실패한다."""
        try:
            removed = testset_documents.remove(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {**removed, **testset_documents.listing()}

    @router.get("/api/testsets/readiness")
    def testset_readiness() -> dict:
        """측정을 시작할 수 있는 상태인가 — 막혔다면 무엇이 어긋났는지 함께 돌려준다."""
        return testset_ready.readiness()

    @router.get("/api/testsets/facts")
    def list_fact_candidates() -> dict:
        """문서에서 뽑은 사실 후보 — 줄마다 긴 판에도 있는지, 다른 문서에도 같은 값이 나오는지, 정답 표기 후보."""
        return {"candidates": testset_facts.candidates(), "source": testset_facts.load_source(),
                **testset_documents.listing()}

    @router.post("/api/testsets/facts/skeleton")
    def build_source_skeleton(req: SkeletonRequest) -> dict:
        """고른 사실로 빈칸만 남은 원천을 짠다 — 사람이 채우는 칸은 질문·변형, 문서에 없는 사실, 정규식이다."""
        try:
            return testset_facts.skeleton(req.picked, req.canary)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.put("/api/testsets/source")
    def save_source(req: SourceRequest) -> dict:
        """사람이 쓴 원천을 저장한다. 문항과 정답이 들어 있어 세트 폴더 옆(저장소 밖)에 둔다."""
        try:
            return {"saved_to": testset_facts.save_source(req.source)}
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/testsets/derive")
    def derive_testsets(req: DeriveRequest) -> dict:
        """원천 파일 하나를 세트 넷(+긴 컨텍스트)으로 펼치고 검사한다. `write`면 **새 판을 낸다** —
        `testsets/versions/<만든 때>/`에 쌓이고 실행은 가장 최근 판을 읽는다. `error`가 하나라도 있으면 내지 않는다."""
        try:
            sets, checks = testset_derive.derive(req.source, keep_closed_qa=req.keep_closed_qa)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"원천을 읽지 못했다: {exc}") from exc
        errors = [c for c in checks if c["level"] == testset_derive.ERROR]
        published = {"version": None, "written": [], "versions": len(quality_testsets.versions())}
        if req.write and not errors:
            published = testset_derive.publish(sets)
        return {"sets": sets, "checks": checks, "cells": testset_derive.counts(sets),
                **published, "blocked": bool(errors and req.write)}

    @router.get("/api/testsets/documents")
    def list_testset_documents() -> dict:
        """놓인 문서와 짝 상태 — 짧은 판만 있는 문서는 선정용 실행에서 그 항목이 실패할 자리다."""
        return testset_documents.listing()

    @router.post("/api/testsets/documents/place")
    def place_testset_documents(req: PlaceDocumentsRequest) -> dict:
        """받은 폴더의 `.md` 자리를 정하고, `write`면 놓는다. **못 정한 것이 있으면 놓지 않는다** —
        이름이 같은 묶음이 한 문서의 두 판이고, 그중 짧은 쪽이 짧은 판, canary가 든 파일이 인젝션 판이다."""
        incoming = Path(req.incoming)
        if not incoming.is_dir():
            raise HTTPException(status_code=404, detail=f"폴더가 없다: {req.incoming}")
        planned = testset_documents.plan(incoming, req.canary)
        placed = []
        if req.write:
            try:
                placed = testset_documents.apply(incoming, planned)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {**planned, "placed": placed, **testset_documents.listing()}

    return router
