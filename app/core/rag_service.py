"""
RAG 조회 서비스 (ChromaDB 클라이언트-서버 + text-embedding-3-small)

[배경] app/core/rag_builder.py는 policy_data_master.yaml을 ChromaDB로 임베딩하는 코드가 완성되어 있었으나 호출부가 0건이고 chromadb 의존성 자체가 pyproject에 없어, 인덱스가 한 번도 빌드된 적이 없었다. "RAG Vector Engine"은 문서에만 존재하는 유령 컴포넌트였다.
[실행 방식 - 임베디드가 아니라 클라이언트-서버]
chromadb 풀 패키지는 bcrypt>=4.0.1을 요구하는데 이 프로젝트는 passlib 1.7.4 호환을 위해 bcrypt==3.2.2로 고정되어 있어 함께 설치할 수 없다(설치 강행 시 로그인이 깨진다).
docker-compose에 이미 정의된 chroma-server 컨테이너에 얇은 chromadb-client로 붙는다.
무거운 의존성은 전부 Chroma 컨테이너 안에 있으므로 백엔드와 충돌하지 않고, 워커를 스케일 아웃해도 인덱스 하나를 공유한다.

[설계 원칙 - RAG는 점수를 정하지 않는다]
UBCI 점수·등급은 UBCI_Specification 매트릭스 산식이 결정론적으로 산출한다. RAG는 이미 확정된 감점에 **근거 조항을 찾아 붙이는 역할(grounding)만** 한다. 검색 결과가 점수에 영향을 주면 같은 도서가 실행할 때마다 다른 등급을 받게 되어 감사 추적성이 깨진다.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

COLLECTION_NAME = "wms_policy_kb"

_client = None
_embedder = None
_lock = threading.Lock()
_unavailable = False


def _chroma_host_port() -> tuple[str, int]:
    # 컨테이너 안에서는 서비스명(chroma-server:8000), 호스트에서는 localhost:8001로 붙는다.
    host = os.getenv("CHROMA_SERVER_HOST", "localhost")
    port = int(
        os.getenv("CHROMA_SERVER_PORT", "8001" if host == "localhost" else "8000")
    )
    return host, port


def get_client():
    """Chroma HTTP 클라이언트를 지연 생성한다. 서버가 없으면 None (fail-open)."""
    global _client, _unavailable
    if _client is not None or _unavailable:
        return _client

    with _lock:
        if _client is not None or _unavailable:
            return _client
        try:
            import chromadb

            host, port = _chroma_host_port()
            client = chromadb.HttpClient(host=host, port=port)
            client.heartbeat()
            _client = client
            logger.info(f"[RAG] Chroma 서버 연결 완료 ({host}:{port})")
        except Exception as e:
            logger.warning(
                f"[RAG] Chroma 서버 연결 실패({e}) - 근거 인용 없이 진행합니다."
            )
            _unavailable = True
    return _client


def get_embedder():
    """
    OpenAI 임베딩 모델. 키가 없거나 실패하면 None (fail-open).

    API 키는 settings(.env)에서 명시적으로 읽어 넘긴다. CLI로 빌더를 직접 실행할 때는 환경변수가 주입되어 있지 않아 OPENAI_API_KEY만 바라보면 초기화에 실패한다.
    """
    global _embedder
    if _embedder is None:
        try:
            from langchain_openai import OpenAIEmbeddings

            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                try:
                    from app.core.config import settings

                    api_key = getattr(settings, "OPENAI_API_KEY", None)
                except Exception:
                    api_key = None

            _embedder = (
                OpenAIEmbeddings(model="text-embedding-3-small", api_key=api_key)
                if api_key
                else OpenAIEmbeddings(model="text-embedding-3-small")
            )
        except Exception as e:
            logger.warning(f"[RAG] 임베딩 모델 초기화 실패({e})")
    return _embedder


def get_collection():
    client = get_client()
    if client is None:
        return None
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception:
        # 인덱스가 아직 빌드되지 않은 상태 - 조용히 비활성화한다.
        return None


def search_policy(
    query: str, k: int = 3, where: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    규정집에서 질의와 가장 관련 있는 조항을 검색한다.
    반환: [{chunk_id, doc_title, clause_ref, authority_level, content, similarity}]
    실패 시 빈 리스트 (fail-open - RAG는 부가 기능이므로 검수를 멈추게 하지 않는다).
    """
    col = get_collection()
    emb = get_embedder()
    if col is None or emb is None or not query:
        return []

    try:
        vec = emb.embed_query(query)
        res = col.query(
            query_embeddings=[vec],
            n_results=k,
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        logger.warning(f"[RAG] 검색 실패({e})")
        return []

    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    out: List[Dict[str, Any]] = []
    for doc, md, dist in zip(docs, metas, dists):
        md = md or {}
        # Chroma는 거리(distance)를 반환한다. 코사인 거리를 0~1 유사도로 환산해 호출부가 임계값 판단을 직관적으로 할 수 있게 한다.
        similarity = max(0.0, 1.0 - float(dist))
        out.append(
            {
                "chunk_id": md.get("chunk_id"),
                "doc_title": md.get("doc_title"),
                "clause_ref": md.get("clause_ref"),
                "authority_level": md.get("authority_level"),
                # 규범적 강제성 순위 (1=법령 ... 5=내부 실행 기준). 정책서 제0조의2 ①.
                # 검색 정렬에는 쓰지 않는다 - 조항 간 충돌 조정과 판정 로그 기록용이다.
                "authority_rank": md.get("authority_rank"),
                "category": md.get("category"),
                "content": doc,
                "similarity": round(similarity, 4),
            }
        )
    return out


# 결함 코드 -> 규정 검색 질의문.
# "DMG_INT_DOODLE" 같은 내부 코드를 그대로 임베딩하면 규정 문서의 자연어와 매칭되지 않으므로, 규정집에서 실제로 쓰는 표현으로 번역한 질의를 사용한다.
_DEDUCTION_QUERY_MAP = {
    "DMG_INT_DOODLE": "중고 도서 내지 필기 밑줄 낙서 감점 기준",
    "DMG_INT_STAIN": "중고 도서 내지 오염 얼룩 감점 기준",
    "DMG_EXT_CRUSH": "도서 표지 모서리 눌림 찍힘 감점 기준",
    "DMG_EXT_WET": "도서 침수 젖음 페이지 휨 치명적 결함 즉시 반려",
    "DMG_EXT_TEAR": "도서 표지 찢어짐 파본 감점 기준",
    "DMG_INT_DISCOLOR": "도서 내지 황변 변색 감점 기준",
    "DMG_EXT_SCRATCH": "도서 표지 긁힘 스크래치 감점 기준",
    "DMG_EXT_STICKER": "도서 스티커 가격표 자국 감점 기준",
    "DMG_EDGE_WEAR": "도서 모서리 마모 사용감 감점 기준",
    "DMG_SPINE_CRACK": "도서 책등 갈라짐 제본 손상 감점 기준",
    "DMG_BINDING_LOOSE": "도서 제본 벌어짐 낱장 분리 반려 기준",
    "DMG_SIGNATURE": "도서 이름 서명 기재 감점 기준",
    "DMG_STAMP": "도서관 장서인 도장 날인 감점 기준",
}


def cite_deduction_basis(
    defect_type: str, label: str = "", min_similarity: float = 0.20
) -> Optional[Dict[str, Any]]:
    """
    [기능 A] 확정된 감점 항목의 근거 조항을 찾아 반환한다.

    점수는 이미 Policy Agent의 결정론적 산식이 확정한 뒤이며, 이 함수는 그 감점의 출처를
    규정집에서 찾아 붙이기만 한다. 유사도가 임계값에 못 미치면 None을 반환해 관련 없는
    조항을 억지로 인용하지 않는다(근거 없는 인용은 없느니만 못하다).
    """
    query = (
        _DEDUCTION_QUERY_MAP.get(str(defect_type or ""))
        or label
        or str(defect_type or "")
    )
    if not query:
        return None

    hits = search_policy(query, k=1)
    if not hits or hits[0]["similarity"] < min_similarity:
        return None

    top = hits[0]
    return {
        "chunk_id": top["chunk_id"],
        "doc_title": top["doc_title"],
        "clause_ref": top["clause_ref"],
        "authority_level": top["authority_level"],
        "authority_rank": top.get("authority_rank"),
        # 상세화면/보증서에 그대로 노출되는 근거 문구
        "excerpt": (top["content"] or "").split("상세 내용:")[-1].strip()[:220],
        "similarity": top["similarity"],
    }


def build_hitl_briefing(
    book_title: str,
    ubci_score: Optional[int],
    suggested_grade: Optional[str],
    defects: List[Dict[str, Any]],
    critic_reason: Optional[str] = None,
    similar_cases: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    [기능 B] HITL 결재 관리자 보조 브리핑.

    관리자가 애매한 건을 결재할 때 필요한 세 가지를 모아 준다.
      1) 관련 규정 조항 (RAG 검색)
      2) 유사 과거 판정 사례 (호출부가 DB에서 조회해 넘겨줌 - 이건 RAG가 아니라 SQL이다)
      3) 위 둘을 근거로 한 판단 재료 정리 (GPT-4o-mini)

    LLM이 결재를 대신하지 않도록 프롬프트에서 "결정하지 말고 판단 재료를 정리하라"고 명시한다. 최종 결정 권한은 사람에게 있다.
    """
    import json

    defect_types = sorted(
        {str(d.get("type")) for d in (defects or []) if d.get("type")}
    )
    query = " ".join(
        filter(
            None,
            [
                book_title or "중고 도서",
                f"UBCI {ubci_score}점" if ubci_score is not None else "",
                " ".join(_DEDUCTION_QUERY_MAP.get(t, t) for t in defect_types),
                "중고 도서 매입 등급 판정 반품 수용 기준",
            ],
        )
    ).strip()

    clauses = search_policy(query, k=4)

    recommendation = None
    try:
        from app.ai.agents import llm_mini

        if llm_mini:
            prompt = f"""당신은 중고도서 물류센터 HITL 결재 관리자를 보조하는 분석가입니다.
아래 자료를 정리해 관리자가 판단하기 쉽게 요약하세요. **당신이 결재를 결정하지 마세요.**
판단 재료를 정리하고 고려할 쟁점을 짚어주는 것까지가 역할입니다.

[검수 건]
- 도서명: {book_title or "미상"}
- AI 산출 UBCI: {ubci_score}점 (제안 등급: {suggested_grade})
- 검출 결함: {", ".join(defect_types) or "없음"}
- HITL 이관 사유: {critic_reason or "미기재"}

[관련 규정 조항]
{json.dumps([{k: c.get(k) for k in ("doc_title", "clause_ref", "content")} for c in clauses], ensure_ascii=False, indent=1)}

[유사 과거 판정 사례]
{json.dumps(similar_cases or [], ensure_ascii=False, indent=1)}

[출력 형식]
- 3~5문장 한국어 산문. 목록 기호 없이.
- 규정을 인용할 때는 조항명을 그대로 밝힐 것.
- 과거 사례와 이번 건이 다른 점이 있으면 반드시 지적할 것.
- 근거가 부족하면 "규정상 명확한 근거가 없다"고 솔직히 쓸 것. 없는 조항을 지어내지 말 것.
"""
            recommendation = llm_mini.invoke(prompt).content
    except Exception as e:
        logger.warning(f"[RAG] HITL 브리핑 생성 실패({e}) - 규정 조항만 반환합니다.")

    return {
        "related_clauses": clauses,
        "similar_cases": similar_cases or [],
        "recommendation": recommendation,
        "disclaimer": "본 브리핑은 판단 보조 자료이며 최종 결재 권한은 관리자에게 있습니다.",
    }
