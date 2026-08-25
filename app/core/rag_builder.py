"""
RAG 지식베이스 인덱스 빌더 (policy_data_master.yaml -> ChromaDB)

실행:
    .venv/Scripts/python.exe -m app.core.rag_builder            # 증분(이미 있으면 스킵)
    .venv/Scripts/python.exe -m app.core.rag_builder --rebuild  # 컬렉션 삭제 후 재빌드

[수정 이력] 종전 구현은 langchain_community의 Chroma를 임베디드 모드
(persist_directory=...)로 사용했다. 그러나 chromadb 풀 패키지는 bcrypt>=4.0.1을 요구하는데
이 프로젝트는 passlib 호환을 위해 bcrypt==3.2.2로 고정되어 있어 애초에 설치가 불가능했고,
그 결과 이 파일은 호출부 0건 + 의존성 미설치 상태로 한 번도 실행된 적이 없었다.
docker-compose에 이미 정의되어 있던 chroma-server 컨테이너에 얇은 chromadb-client로
붙는 클라이언트-서버 방식으로 교체한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import re

import yaml
import logging

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
KB_DIR = BASE_DIR / "docs" / "ai_knowledge_base"
YAML_PATH = KB_DIR / "policy_data_master.yaml"
# ConfigMap 키는 ASCII만 허용되므로(k8s 제약) 배포 환경에서는 wms_sop.md로 마운트된다.
SOP_CANDIDATES = ["WMS_표준_운영_정책서.md", "wms_sop.md"]

EMBED_BATCH = 64

# 정책 권위 레벨 -> authority_rank (WMS 표준 운영 정책서 제0조의2 ①).
# 숫자가 낮을수록 규범적 우선순위가 높다. 충돌 조정용 값이며 검색 정렬 키가 아니다
# (검색은 의미 유사도로 하고, rank는 판정 로그에 남겨 근거의 강제성을 밝힌다).
AUTHORITY_RANK = {
    "Statute": 1,
    "Contract": 2,
    "Policy": 3,
    "Guideline": 4,
    "Internal": 5,
}


def load_yaml_knowledge() -> List[Dict[str, Any]]:
    """YAML 형태의 규정집을 읽어서 반환합니다."""
    if not YAML_PATH.exists():
        raise FileNotFoundError(f"YAML 지식 베이스를 찾을 수 없습니다: {YAML_PATH}")
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def to_documents(data: List[Dict[str, Any]]):
    """YAML 청크를 임베딩용 텍스트 + 메타데이터로 변환합니다."""
    ids, texts, metadatas = [], [], []

    for i, item in enumerate(data):
        chunk_id = item.get("chunk_id") or f"chunk_{i}"

        # 임베딩이 맥락을 이해할 수 있도록 제목-조항-배경-본문을 한 덩어리로 구성
        content = (
            f"[{item.get('doc_title')} - {item.get('clause_ref')}]\n"
            f"배경 문맥: {item.get('parent_context')}\n"
            f"상세 내용: {item.get('content')}"
        )

        # Chroma 메타데이터는 스칼라만 허용하므로 리스트(category)는 쉼표로 조인
        level = item.get("authority_level") or ""
        metadata = {
            "chunk_id": chunk_id,
            "platform": item.get("platform") or "",
            "authority_level": level,
            "authority_rank": AUTHORITY_RANK.get(level, 9),
            "category": ",".join(item.get("category") or []),
            "doc_title": item.get("doc_title") or "",
            "clause_ref": item.get("clause_ref") or "",
        }

        ids.append(chunk_id)
        texts.append(content)
        metadatas.append(metadata)

    return ids, texts, metadatas


# 조문 제목 예: "### 제 3조. REJECT 하드 리미트" / "### 제 0조의2. 정책 권위 레벨 및 RAG 정렬 기준"
_SOP_ARTICLE_RE = re.compile(
    r"^###\s+(제\s*\d+조(?:의\d+)?\.?\s*[^\n]*)$", re.MULTILINE
)
_SOP_CHAPTER_RE = re.compile(
    r"^##\s+(제\s*\d+\s*장(?:의\d+)?\.?\s*[^\n]*)$", re.MULTILINE
)


def load_sop_documents():
    """
    WMS 표준 운영 정책서(Markdown)를 조(條) 단위로 잘라 임베딩 대상으로 변환한다.

    [왜 조 단위인가] 정책서는 1100줄 단문서라 통째로 임베딩하면 어떤 질의든 같은 벡터
    하나에 걸려 조항을 특정할 수 없다. 반대로 문단 단위로 더 쪼개면 표(表)로 된 판정
    기준이 행 단위로 흩어져 "제3조 REJECT 하드 리미트" 전체를 못 본다. 조가 판정 근거를
    인용하는 최소 단위이므로 조 경계로 자른다.

    [왜 authority_level=Internal인가] 이 문서는 정책서 제0조의1의 "내부 실행 기준"에
    해당한다. 규범적 우선순위는 법령·약관보다 낮지만(rank 5), AI 임계값과 상태 전이를
    실제로 규정하는 문서라 감점 근거 인용에서는 가장 자주 인용된다.
    """
    sop_path = next((KB_DIR / n for n in SOP_CANDIDATES if (KB_DIR / n).exists()), None)
    if sop_path is None:
        logger.warning(f"[RAG] 표준 운영 정책서를 찾을 수 없어 건너뜁니다: {KB_DIR}")
        return [], [], []

    text = sop_path.read_text(encoding="utf-8")

    # 조문 시작 위치마다 소속 장(章)을 기록해 parent_context로 쓴다.
    chapters = [(m.start(), m.group(1).strip()) for m in _SOP_CHAPTER_RE.finditer(text)]

    def chapter_of(pos: int) -> str:
        current = ""
        for start, title in chapters:
            if start < pos:
                current = title
            else:
                break
        return current

    matches = list(_SOP_ARTICLE_RE.finditer(text))
    ids, texts, metadatas = [], [], []

    for i, m in enumerate(matches):
        heading = m.group(1).strip().rstrip(".")
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if not body:
            continue

        # "제 3조. REJECT 하드 리미트" -> clause_ref "제3조", 제목은 별도 보관
        num_match = re.match(r"제\s*(\d+)조(?:의(\d+))?\.?\s*(.*)", heading)
        if num_match:
            art, sub, title = (
                num_match.group(1),
                num_match.group(2),
                num_match.group(3).strip(),
            )
            clause_ref = f"제{art}조" + (f"의{sub}" if sub else "")
            chunk_id = f"wms_sop_art{art}" + (f"_{sub}" if sub else "")
        else:
            clause_ref, title, chunk_id = heading, heading, f"wms_sop_{i}"

        chapter = chapter_of(m.start())

        content = (
            f"[WMS 표준 운영 정책서 - {clause_ref} {title}]\n"
            f"배경 문맥: {chapter}\n"
            f"상세 내용: {body}"
        )

        ids.append(chunk_id)
        texts.append(content)
        metadatas.append(
            {
                "chunk_id": chunk_id,
                "platform": "Common",
                "authority_level": "Internal",
                "authority_rank": AUTHORITY_RANK["Internal"],
                "category": chapter,
                "doc_title": "WMS 표준 운영 정책서",
                "clause_ref": f"{clause_ref} {title}".strip(),
            }
        )

    return ids, texts, metadatas


def build_vector_db(rebuild: bool = False):
    """YAML 지식 베이스를 파싱하여 ChromaDB 서버에 임베딩 및 저장합니다."""
    from app.core.rag_service import COLLECTION_NAME, get_client, get_embedder

    client = get_client()
    if client is None:
        raise RuntimeError(
            "Chroma 서버에 연결할 수 없습니다. `docker compose up -d chroma-server` 후 재시도하세요."
        )

    embedder = get_embedder()
    if embedder is None:
        raise RuntimeError(
            "OpenAI 임베딩 모델을 초기화할 수 없습니다. OPENAI_API_KEY를 확인하세요."
        )

    if rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"기존 컬렉션 삭제: {COLLECTION_NAME}")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        # 규정 조항 검색에는 코사인 유사도가 적합하다 (문서 길이 편차의 영향을 줄임).
        metadata={"hnsw:space": "cosine"},
    )

    # 지식베이스는 두 축이다.
    #   ① policy_data_master.yaml - 외부 법령·약관·플랫폼 운영정책 (규범적 근거)
    #   ② WMS_표준_운영_정책서.md  - 내부 실행 기준 (AI 임계값·상태 전이·SLA)
    # 감점 근거 인용은 대부분 ②를 가리키고, ①은 반품 가능 여부·비용 부담 같은 거래 조건을 다룰 때 인용된다.
    data = load_yaml_knowledge()
    ids, texts, metadatas = to_documents(data)

    sop_ids, sop_texts, sop_metas = load_sop_documents()
    if sop_ids:
        print(f"표준 운영 정책서 {len(sop_ids)}개 조문을 인덱스에 포함합니다.")
        ids += sop_ids
        texts += sop_texts
        metadatas += sop_metas

    existing = collection.count()

    if existing >= len(ids) and not rebuild:
        print(f"이미 인덱싱됨 ({existing}건). 재빌드하려면 --rebuild 를 사용하세요.")
        return collection

    print(f"총 {len(ids)}개 정책 청크를 임베딩합니다 (batch={EMBED_BATCH})...")
    for start in range(0, len(ids), EMBED_BATCH):
        end = start + EMBED_BATCH
        batch_texts = texts[start:end]
        vectors = embedder.embed_documents(batch_texts)
        collection.upsert(
            ids=ids[start:end],
            documents=batch_texts,
            metadatas=metadatas[start:end],
            embeddings=vectors,
        )
        print(f"  {min(end, len(ids))}/{len(ids)} 완료")

    print(
        f"ChromaDB 인덱스 구축 완료: 컬렉션 '{COLLECTION_NAME}', 총 {collection.count()}건"
    )
    return collection


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rebuild", action="store_true", help="컬렉션을 삭제하고 처음부터 재빌드"
    )
    args = parser.parse_args()
    build_vector_db(rebuild=args.rebuild)
