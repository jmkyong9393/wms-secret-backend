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

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
YAML_PATH = BASE_DIR / "docs" / "ai_knowledge_base" / "policy_data_master.yaml"

EMBED_BATCH = 64


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
        metadata = {
            "chunk_id": chunk_id,
            "platform": item.get("platform") or "",
            "authority_level": item.get("authority_level") or "",
            "category": ",".join(item.get("category") or []),
            "doc_title": item.get("doc_title") or "",
            "clause_ref": item.get("clause_ref") or "",
        }

        ids.append(chunk_id)
        texts.append(content)
        metadatas.append(metadata)

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
        raise RuntimeError("OpenAI 임베딩 모델을 초기화할 수 없습니다. OPENAI_API_KEY를 확인하세요.")

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

    data = load_yaml_knowledge()
    ids, texts, metadatas = to_documents(data)
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

    print(f"ChromaDB 인덱스 구축 완료: 컬렉션 '{COLLECTION_NAME}', 총 {collection.count()}건")
    return collection


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild", action="store_true", help="컬렉션을 삭제하고 처음부터 재빌드")
    args = parser.parse_args()
    build_vector_db(rebuild=args.rebuild)
