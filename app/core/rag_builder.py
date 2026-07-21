import yaml
from pathlib import Path
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 실제 구축된 마스터 정책 YAML 파일 경로 반영
YAML_PATH = BASE_DIR / "docs" / "ai_knowledge_base" / "policy_data_master.yaml"
# 임베딩된 벡터 DB가 저장될 경로
CHROMA_DB_DIR = BASE_DIR / "ai_knowledge_base" / "chroma_db"

def load_yaml_knowledge():
    """YAML 형태의 규정집을 읽어서 반환합니다."""
    if not YAML_PATH.exists():
        raise FileNotFoundError(f"YAML 지식 베이스를 찾을 수 없습니다: {YAML_PATH}")
    
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def build_vector_db():
    """YAML 지식 베이스를 파싱하여 ChromaDB에 임베딩 및 저장합니다."""
    print("🚀 RAG Vector DB (ChromaDB) 빌드를 시작합니다...")
    
    data = load_yaml_knowledge()
    documents = []
    
    # 리스트 형태의 청크 단위 데이터 순회 (policy_data_master.yaml 구조 맞춤)
    for item in data:
        # LLM이 맥락을 가장 잘 이해할 수 있도록 구조화된 텍스트 구성
        content = f"[{item.get('doc_title')} - {item.get('clause_ref')}]\n"
        content += f"배경 문맥: {item.get('parent_context')}\n"
        content += f"상세 내용: {item.get('content')}"
        
        # 메타데이터 추출 (필터링 쿼리에 유용)
        metadata = {
            "chunk_id": item.get("chunk_id", ""),
            "platform": item.get("platform", ""),
            "authority_level": item.get("authority_level", ""),
            "category": ",".join(item.get("category", [])), # 리스트는 쉼표로 조인
            "doc_title": item.get("doc_title", "")
        }
        
        doc = Document(page_content=content, metadata=metadata)
        documents.append(doc)

    # 임베딩 모델 준비 (OpenAI)
    # 실제 실행 시 OPENAI_API_KEY 환경 변수가 필요합니다.
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    
    # ChromaDB 생성 및 디스크 영구 저장(persist)
    print(f"📦 총 {len(documents)}개의 정책 청크(Chunk)를 임베딩합니다...")
    vectorstore = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory=str(CHROMA_DB_DIR)
    )
    
    print(f"✅ ChromaDB 구축 완료! 저장 경로: {CHROMA_DB_DIR}")
    return vectorstore

if __name__ == "__main__":
    build_vector_db()
