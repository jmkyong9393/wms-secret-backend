# -*- coding: utf-8 -*-
"""RAG 검색 지연 실측 — 임베딩 구간과 코사인 검색 구간을 분리해 잰다.

두 구간은 성격이 다르다.
  · 임베딩: OpenAI API 왕복 — 네트워크와 외부 서비스에 좌우된다
  · 코사인 검색: ChromaDB HNSW 조회 — 우리 인덱스의 성능이다
합쳐서 하나로 말하면 "우리 검색이 느리다/빠르다"를 판단할 수 없어 나눠 측정한다.

읽기 전용이다. col.query()만 호출하며 인덱스를 변경하지 않는다.

실행:  .venv/Scripts/python.exe scripts/measure_rag_latency.py
"""
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from app.core.rag_service import COLLECTION_NAME, get_client, get_embedder  # noqa: E402

# 실제 파이프라인이 던지는 질의 유형 그대로 쓴다 (감점 근거 인용 · 거래 처분 · HITL 브리핑)
QUERIES = [
    "내지 낙서가 있는 도서의 감점 기준",
    "커버 찢어짐 심각도별 감점 한도",
    "모서리 마모 누적 감점 상한",
    "액체 오염이 발생한 도서의 처리",
    "중고 도서 반품 수용 조건",
    "배송비 부담 주체 판단 기준",
    "수험서 문제집 낙서 판정 특칙",
    "UBCI 등급 경계와 기본 처분",
    "판매자 귀책 사유 판단",
    "입고 불가 사유와 반송 절차",
]
WARMUP = 3          # 첫 호출은 연결 수립이 섞여 대표값이 아니다
REPEAT = 20         # 질의당 검색 반복
TOP_K = 3           # 운영 기본값 (search_policy의 k)


def pct(xs, p):
    xs = sorted(xs)
    i = min(int(len(xs) * p / 100), len(xs) - 1)
    return xs[i]


def summarize(name, xs, unit='ms'):
    return {
        'name': name, 'n': len(xs), 'unit': unit,
        'min': round(min(xs), 2), 'p50': round(statistics.median(xs), 2),
        'p95': round(pct(xs, 95), 2), 'max': round(max(xs), 2),
        'mean': round(statistics.fmean(xs), 2),
    }


def main():
    col = get_client().get_collection(COLLECTION_NAME)
    emb = get_embedder()
    if col is None or emb is None:
        print('ChromaDB 또는 임베더를 얻지 못했습니다 — 중단')
        return 1
    print(f'컬렉션 {COLLECTION_NAME}: {col.count()}개 청크 · metadata={col.metadata}')

    # ── 1) 임베딩 지연 (질의당 1회) ──────────────────
    vecs, emb_ms = [], []
    for q in QUERIES:
        t = time.perf_counter()
        v = emb.embed_query(q)
        emb_ms.append((time.perf_counter() - t) * 1000)
        vecs.append(v)
    dim = len(vecs[0])

    # ── 2) 코사인 검색 지연 (임베딩 제외, 순수 조회) ──
    for v in vecs[:1]:
        for _ in range(WARMUP):
            col.query(query_embeddings=[v], n_results=TOP_K,
                      include=['documents', 'metadatas', 'distances'])

    search_ms, sims = [], []
    for v in vecs:
        for _ in range(REPEAT):
            t = time.perf_counter()
            res = col.query(query_embeddings=[v], n_results=TOP_K,
                            include=['documents', 'metadatas', 'distances'])
            search_ms.append((time.perf_counter() - t) * 1000)
        d = (res.get('distances') or [[]])[0]
        if d:
            sims.append(1 - d[0])   # 코사인 거리 → 유사도

    e, s = summarize('임베딩(OpenAI 왕복)', emb_ms), summarize('코사인 검색(ChromaDB)', search_ms)
    print(f"\n{'구간':<24}{'n':>5}{'min':>9}{'p50':>9}{'p95':>9}{'max':>9}")
    for r in (e, s):
        print(f"{r['name']:<24}{r['n']:>5}{r['min']:>9}{r['p50']:>9}{r['p95']:>9}{r['max']:>9}")
    print(f"\n임베딩 차원: {dim}")
    print(f"1순위 코사인 유사도: 최저 {min(sims):.3f} / 중앙값 {statistics.median(sims):.3f} / 최고 {max(sims):.3f}")

    kst = datetime.now(timezone(timedelta(hours=9)))
    out = {
        'measured_at_kst': kst.isoformat(timespec='seconds'),
        'environment': 'local docker (wms-secret-chroma) · 라이브 클러스터 아님',
        'collection': COLLECTION_NAME, 'chunks': col.count(),
        'space': (col.metadata or {}).get('hnsw:space'),
        'embedding_model': 'text-embedding-3-small', 'embedding_dim': dim,
        'top_k': TOP_K, 'queries': len(QUERIES), 'repeat_per_query': REPEAT,
        'warmup_excluded': WARMUP,
        'embedding_latency': e, 'search_latency': s,
        'top1_similarity': {'min': round(min(sims), 3),
                            'median': round(statistics.median(sims), 3),
                            'max': round(max(sims), 3)},
    }
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                       'docs', f'rag_latency_{kst:%Y%m%d_%H%M}.json')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n결과 저장: {os.path.normpath(dst)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
