# 🌉 WMS AI Platform API 스키마 명세서 (v1.4.2.0)

본 문서는 프론트엔드(Next.js)와 백엔드(FastAPI) 간의 데이터 통신 규격을 정의한 인터페이스 설계서입니다. 대용량 이미지 처리와 AI의 응답 지연(Latency)을 해결하기 위한 **비동기(Asynchronous) 폴링 패턴**이 적용되어 있습니다.

---

## 1. 도서 기초 정보 조회 (책 스캔 시점)
작업자가 바코드를 스캔했을 때, 도서의 기본 정보(제목, 원가 등)를 화면에 띄워주기 위한 API입니다.

*   **Endpoint:** `GET /api/v1/books/{isbn}`
*   **Description:** ISBN 바코드로 도서 메타데이터를 조회합니다.

**Request Path Parameters:**
| Name | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `isbn` | `string` | O | 13자리 도서 바코드 번호 |

**Response (200 OK):**
```json
{
  "isbn": "9788912345678",
  "title": "해리포터와 마법사의 돌",
  "original_price": 15000,
  "publisher": "문학수첩"
}
```
**Response (404 Not Found):**
```json
{
  "detail": "등록되지 않은 도서입니다."
}
```

---

## 2. 검수 요청 (사진 촬영 및 업로드 시점)
작업자가 사진을 찍고 업로드 버튼을 누를 때 호출됩니다. 빠른 응답을 위해 이미지 크기는 1MB 이하로 압축 전송을 권장하며, 백엔드는 즉시 `inspection_id`(대기표)만 반환하고 비동기 큐(Celery)로 넘깁니다.

*   **Endpoint:** `POST /api/v1/inspections`
*   **Content-Type:** `multipart/form-data`

**Request Body:**
| Name | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `file` | `binary` | O | 촬영된 도서 이미지 (JPEG/PNG) |
| `isbn` | `string` | O | 대상 도서의 ISBN |
| `worker_id` | `string` | X | 작업을 수행한 담당자 ID (선택) |

**Response (202 Accepted):**
```json
{
  "inspection_id": "req_123abc456",
  "status": "PENDING",
  "message": "검수 대기열에 등록되었습니다."
}
```
**Response (422 Unprocessable Entity):**
```json
{
  "detail": "지원하지 않는 파일 형식입니다. (JPEG/PNG만 허용)"
}
```

---

## 3. 검수 상태 및 결과 조회 (폴링 시점)
프론트엔드(React Query)가 발급받은 `inspection_id`를 들고 3~5초 주기로 백엔드에 진행 상태를 물어보는 API입니다. 상태에 따라 응답 구조가 다릅니다.

*   **Endpoint:** `GET /api/v1/inspections/{inspection_id}`

**Request Path Parameters:**
| Name | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `inspection_id` | `string` | O | 검수 요청 시 발급받은 고유 ID |

**Response Case A (검수 진행 중 - 200 OK):**
```json
{
  "inspection_id": "req_123abc456",
  "status": "PROCESSING",
  "message": "AI Vision 에이전트가 훼손 부위를 분석 중입니다..."
}
```

**Response Case B (검수 완료 - 200 OK):**
*프론트엔드의 화면 좌표 틀어짐 방지를 위해, `bbox`의 모든 값은 절대 픽셀이 아닌 `0.0 ~ 1.0` 사이의 상대 비율(Relative Ratio)로 제공됩니다.*

```json
{
  "inspection_id": "req_123abc456",
  "status": "COMPLETED",
  "isbn": "9788912345678",
  "final_grade": "C",          // 판정 등급 (S, A, B, C, REJECT)
  "estimated_refund": 8500,    // AI가 산정한 예상 환불가 (원가 대비 차감)
  "ai_reasoning": "책 하단에 전체 길이의 15% 크기에 해당하는 찢어짐이 발견되어 C등급으로 판정합니다.", // 고객에게 안내될 사유
  "defects": [
    {
      "type": "TEAR",          // 결함 종류 (TEAR, STAIN, SCRATCH 등)
      "bbox": { 
        "x": 0.15, 
        "y": 0.80, 
        "width": 0.20, 
        "height": 0.05 
      } 
    }
  ]
}
```

**Response Case C (검수 실패 / 거절 - 200 OK):**
```json
{
  "inspection_id": "req_123abc456",
  "status": "FAILED",
  "final_grade": "REJECT",
  "ai_reasoning": "이미지 내에서 도서를 인식할 수 없거나, 과도한 흔들림으로 판독이 불가합니다. 재촬영을 진행해 주세요."
}
```
