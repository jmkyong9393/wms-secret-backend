"""
====================================================================
[WMS B2B Vision AI - WBF (Weighted Boxes Fusion) 앙상블 탐지 모듈]
- Stage 0: yolov8n_coco_roi.pt (COCO book cls=73) -> 책 ROI 크롭 게이트 [2026-08-06 비활성]
- Model 1: yolov8_high_recall_best.pt (conf=0.12, weight=1.0) -> 미세 흠집 미탐 방지 (Recall 0.560~0.576 실측 @conf=0.25)
- Model 2: yolov8_high_precision_base.pt (conf=0.25, weight=1.5) -> Precision 91.2%
- Model 3: yolov8_doodle_ocr.pt (conf=0.20, weight=1.0) -> 손글씨/낙서 패치 전담 (mAP50 84.2%)
- 순수 NumPy 기반 WBF 알고리즘 구현 (Zero-Dependency & 고속 인메모리 융합)

[ROI 크롭 게이트 도입 배경 - 2026-08-05]
결함 3모델은 전부 "책이 프레임을 가득 채운 근접 촬영" 도메인(Roboflow/AIHub 패치)에서
학습됐다. 촬영 원본(작업자 손·책상·배경 포함)을 그대로 넣으면 배경 텍스처(머리카락,
가구 등)를 찢어짐/낙서로 오탐하고, 책이 작게 찍힐수록 미세 결함이 픽셀에서 증발한다.
특히 Model 3은 명세서상 "손글씨 전용 픽셀 패치 모듈"로 설계됐음에도 풀프레임을 받고
있었다. Stage 0에서 책 영역만 잘라 3모델에 공급함으로써 학습 도메인과 추론 입력을
일치시킨다. 책 미탐지 시에는 기존과 동일하게 풀프레임으로 폴백한다(탐지 누락 방지).

[정정 - 2026-08-06] 위 전제가 실측으로 반증되어 게이트를 비활성했다 (History §14).

  1) "학습 도메인 = 책이 프레임을 가득 채운 근접 촬영"이 사실이 아니다. Roboflow
     학습본 811장을 표본 조사한 결과 **사람이 찍은 사진 그대로**이며 책이 프레임의
     60~80%를 차지하고 책상 배경이 남아 있다. ROI 크롭(패딩 4%)은 책이 92%를 채우므로
     학습 도메인에 맞추는 것이 아니라 오히려 벗어나게 만든다.
  2) COCO book 게이트는 팀 실촬영 71장 중 **26장(37%)** 에서만 책을 찾았다. 실패한
     45장은 전부 "미검출"이고 ROI 과소로 걸러진 것은 0장이다 - 크기 문제가 아니라
     COCO가 이 사진들을 책으로 보지 않는다(책배·속지 촬영본, 프레임을 꽉 채운 표지컷).
  3) 크롭 유무를 동일 이미지에 직접 대조한 결과 적중 차이는 ±1~2개이고 방향도 일정하지
     않다. 크롭이 이득이라는 증거가 없다.

  스케일 불일치(팀 실촬영 결함이 프레임 대비 2.35% vs Roboflow 3.3~5.6%)는 사후 크롭이
  아니라 **촬영 규격**(책상 평면, 책이 화면을 채우게)으로 해소한다.
  코드는 지우지 않고 USE_ROI_GATE 플래그로 남겨 A/B 가능하게 둔다.
====================================================================
"""

import json
import os
import re
from datetime import datetime

import cv2
import numpy as np
from pathlib import Path
from ultralytics import YOLO
from typing import List, Dict, Any, Tuple, Optional

# 모델 파일 절대 경로
AI_DIR = Path(__file__).parent
MODEL_RECALL_PATH = AI_DIR / "yolov8_high_recall_best.pt"
MODEL_PRECISION_PATH = AI_DIR / "yolov8_high_precision_base.pt"
MODEL_DOODLE_PATH = AI_DIR / "yolov8_doodle_ocr.pt"
# COCO 사전학습 nano 모델 - 결함 탐지가 아니라 "책이 어디 있는가"(cls 73)만 담당
MODEL_BOOK_ROI_PATH = AI_DIR / "yolov8n_coco_roi.pt"

# COCO 클래스 73 = book
COCO_BOOK_CLASS_ID = 73

# ====================================================================
# [2026-08-06 Phase 1] Stage 0 ROI 게이트 비활성화 + 추론 해상도 640 정렬
# ====================================================================
# 결함 3모델의 학습셋(Roboflow)은 **크롭본이 아니라 사람이 찍은 사진 그대로**이며,
# 책이 프레임의 60~80%를 차지하고 책상 배경이 남아 있다. 반면 Stage 0 ROI 크롭은
# 패딩 4%라 책이 92%를 채워, 크롭이 학습 도메인에 맞추는 것이 아니라 **오히려
# 벗어나게** 만든다.
#
# 실측 근거 (History §14.1 / §14.3):
#   - COCO book 게이트는 팀 실촬영 71장 중 26장(37%)에서만 책을 찾았고,
#     실패 45장은 전부 "미검출"이었다 (ROI 과소 0장). 크기 문제가 아니라
#     COCO가 우리 사진을 책으로 보지 않는다.
#   - 크롭 유무를 직접 대조한 결과 적중 차이는 ±1~2개이고 방향도 일정하지 않다.
#     크롭이 이득이라는 증거가 없다.
#
# 코드는 지우지 않고 플래그로 남긴다 - 새 촬영 규격으로 테스트하면서 A/B가 필요하다.
USE_ROI_GATE = False

# 결함 3모델의 추론 해상도. Model 1(v6_recall)의 학습 해상도가 640이므로 일치시킨다.
# 동일 가중치로 해상도만 올려 측정하면 Roboflow mAP50이 0.530(@640) -> 0.464(@1024)로
# 12~19% 하락한다. 원본이 640인 데이터를 키우는 것은 정보를 더하지 않고 보간 흐림만 만든다.
DEFECT_IMGSZ = 640

# ====================================================================
# [Track 2·3] VLM 크롭 실측 데이터 적재
# ====================================================================
# VLM이 속지/책배를 제대로 잘랐는지는 **눈으로 확인해야만** 알 수 있다. 좌표만 로그로
# 남기면 "박스가 나왔다"는 사실만 알 뿐 그 박스가 맞는지는 판단할 수 없다.
# 크롭 결과물 자체를 쌓아 두어야 나중에 검증셋으로 쓸 수 있다.
#
# 운영 환경에서는 디스크가 무한정 늘어나면 안 되므로 환경변수로 끈다.
# 기본값은 켜짐 - 지금은 데이터를 모으는 단계다.
SAVE_VLM_CROPS = os.getenv("WMS_SAVE_VLM_CROPS", "1") not in ("0", "false", "False")
VLM_CROP_DIR = Path(os.getenv("WMS_VLM_CROP_DIR", str(AI_DIR / "_vlm_crops")))


# ====================================================================
# [Track 2·3] 지면 영역 검출 — YOLO-World (오픈 보캐뷸러리)
# ====================================================================
# 속지 크롭 좌표를 VLM에게 받아 봤으나 정확도가 낮았다. 실측: GPT-4o가 반환한 박스는
# {xmin:50, ymin:100, xmax:950, ymax:900} 처럼 **전부 반올림된 숫자**로, 실제 지면 경계를
# 찾은 것이 아니라 "가운데 90%x80%" 정도를 추측한 값이었다. 그 결과 책상과 손이 크롭에
# 그대로 남았다.
#
# YOLO-World는 텍스트 프롬프트로 대상을 지정하는 검출기라 COCO 고정 클래스의 한계가 없다.
# 실측 비교 (팀 실촬영 6장, 프롬프트 "book", imgsz=640):
#   yolov8s-worldv2 : conf 0.18, 박스가 왼쪽 페이지만 (x 0~517) - 사용 불가
#   yolov8x-worldv2 : conf 0.48~0.95, 6장 전부 책 영역만 정확히 포착 (손·책상 제외)
# 체급이 성능을 갈랐다. imgsz는 640이 가장 좋다(YOLO-World 학습 해상도).
#
# 역할 분리: VLM은 "몇 번 사진이 속지인가"(의미 판단), YOLO-World는 "정확히 어디인가"
# (공간 판단)를 맡는다. 각 도구의 강점에 맞춘 배치다.
#
# 모델은 지연 로딩한다 - 속지 컷이 없는 검수(대부분)에서는 로드조차 하지 않는다.
PAGE_REGION_MODEL = os.getenv("WMS_PAGE_REGION_MODEL", "yolov8x-worldv2.pt")
PAGE_REGION_PROMPT = os.getenv("WMS_PAGE_REGION_PROMPT", "book")
PAGE_REGION_CONF = float(os.getenv("WMS_PAGE_REGION_CONF", "0.10"))

_page_region_model = None


def _get_page_region_model():
    """YOLO-World 지연 로딩. 실패해도 예외를 던지지 않는다(호출부에서 VLM 좌표로 폴백)."""
    global _page_region_model
    if _page_region_model is None:
        try:
            from ultralytics import YOLOWorld
            m = YOLOWorld(PAGE_REGION_MODEL)
            m.set_classes([PAGE_REGION_PROMPT])
            _page_region_model = m
            print(f"[Page Region] YOLO-World 로드 완료 ({PAGE_REGION_MODEL}, prompt='{PAGE_REGION_PROMPT}')")
        except Exception as e:
            print(f"[Page Region] YOLO-World 로드 실패({type(e).__name__}) - VLM 좌표로 폴백: {e}")
            _page_region_model = False  # 재시도 방지
    return _page_region_model or None


# ── 손 제거를 시도했다가 철회한 이력 (2026-08-06) ──────────────────────────
#
# 크롭에 손이 남는 것을 없애려고 COCO person 박스로 책 박스의 위/아래 가장자리를
# 잘라내는 로직을 넣었다가 **철회했다.**
#
# 실측(팀 실촬영 6장): 5장에서 손이 제거됐으나, neg_006에서 손이 책 모서리를 감싸 쥔
# 구도라 손을 피하는 과정에서 **표지 하단까지 잘려나갔다**. 이 경로의 목적은 찢어짐·마모
# 탐지이므로 책 영역이 소실되면 결함 자체를 놓친다. 손을 지우자고 결함을 놓치는 것은
# 본말전도다.
#
# 근본적으로 **축정렬 박스로는 "책은 보존하면서 손만 제거"가 성립하지 않는다.**
# 손이 책을 쥐고 있으면 손 영역을 빼는 순간 그 아래 책 영역도 함께 날아간다.
# YOLO-World가 반환하는 책 박스는 이미 책 경계까지만 잡으므로 그 바깥의 손·책상은
# 자동으로 제외되며, 안쪽으로 더 깎는 것은 책을 훼손하는 것 이상의 의미가 없다.
#
# 손 픽셀을 실제로 지우려면 박스가 아니라 **세그멘테이션 마스크**가 필요하다.
# 다만 현재 관측된 doodle 오탐은 전부 인쇄 본문 위에 있었고 손 위가 아니었으므로,
# 손 제거의 실익이 확인되지 않은 상태다. 필요해지면 그때 검토한다.


def detect_page_region(image_bgr: np.ndarray) -> Optional[Tuple[float, float, float, float]]:
    """이미지에서 책 지면 영역을 찾아 정규화 좌표 (x1, y1, x2, y2)를 반환한다.

    여러 개가 잡히면 신뢰도가 가장 높은 박스를 채택하고, 손이 가장자리에 얕게 걸쳐
    있으면 그 띠를 잘라낸다. 찾지 못하면 None - 호출부는 VLM이 준 좌표로 폴백한다.
    """
    model = _get_page_region_model()
    if model is None or image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return None
    try:
        # imgsz=640 고정. 1024로 올리면 오히려 정확도가 떨어진다(학습 해상도가 640).
        r = model.predict(image_bgr, conf=PAGE_REGION_CONF, imgsz=640, verbose=False)[0]
        if len(r.boxes) == 0:
            return None
        confs = r.boxes.conf.cpu().numpy()
        xyxyn = r.boxes.xyxyn.cpu().numpy()
        i = int(np.argmax(confs))
        x1, y1, x2, y2 = [float(v) for v in xyxyn[i]]
        if x2 <= x1 or y2 <= y1:
            return None
        # 박스를 안쪽으로 더 깎지 않는다 - 책 영역이 소실되면 찢어짐·마모를 놓친다
        # (위 '손 제거 철회 이력' 주석 참조).
        return (max(0.0, x1), max(0.0, y1), min(1.0, x2), min(1.0, y2))
    except Exception as e:
        print(f"[Page Region] 지면 검출 실패({type(e).__name__}) - VLM 좌표로 폴백: {e}")
        return None


def save_vlm_crop(
    image_bgr: np.ndarray,
    tag: str,
    detections: Optional[List[Dict[str, Any]]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """VLM이 잘라낸 크롭본과 그 위의 탐지 결과를 로컬에 남긴다.

    산출물 3종 (파일명 접두사 공유):
      {stamp}_{tag}.jpg          크롭 원본 - 잘린 위치가 맞는지 육안 확인용
      {stamp}_{tag}_boxed.jpg    탐지 박스를 그린 사본 - 낙서를 제대로 찍었는지 확인용
      {stamp}_{tag}.json         메타데이터 (원본 파일, VLM이 준 박스, 사진 종류, 탐지 결과)

    저장 실패가 검수를 막아서는 안 되므로 모든 예외를 삼킨다(fail-open).
    """
    if not SAVE_VLM_CROPS or image_bgr is None or getattr(image_bgr, "size", 0) == 0:
        return None
    try:
        VLM_CROP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        safe_tag = re.sub(r"[^0-9A-Za-z가-힣_.-]", "_", str(tag))[:60]
        base = VLM_CROP_DIR / f"{stamp}_{safe_tag}"

        cv2.imwrite(str(base.with_suffix(".jpg")), image_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])

        # 탐지 박스를 그린 사본. bbox는 0~1000 정규화이므로 픽셀로 환산해 그린다.
        if detections:
            h, w = image_bgr.shape[:2]
            boxed = image_bgr.copy()
            for d in detections:
                b = d.get("bbox") or {}
                x1 = int(b.get("xmin", 0) / 1000 * w)
                y1 = int(b.get("ymin", 0) / 1000 * h)
                x2 = int(b.get("xmax", 0) / 1000 * w)
                y2 = int(b.get("ymax", 0) / 1000 * h)
                cv2.rectangle(boxed, (x1, y1), (x2, y2), (0, 200, 255), 2)
                cv2.putText(boxed, f"{d.get('defect_type')} {d.get('confidence')}",
                            (x1, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (0, 200, 255), 1, cv2.LINE_AA)
            cv2.imwrite(str(base.parent / f"{base.name}_boxed.jpg"), boxed,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])

        payload = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "tag": str(tag),
            "crop_size": {"w": int(image_bgr.shape[1]), "h": int(image_bgr.shape[0])},
            "detections": detections or [],
            **(meta or {}),
        }
        base.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return base.with_suffix(".jpg")
    except Exception as e:
        print(f"[WBF Detector] VLM 크롭 저장 실패({type(e).__name__}) - 검수는 계속 진행: {e}")
        return None


def ensure_model_weights() -> None:
    """
    로컬에 없는 YOLO 가중치 파일(.pt)을 S3(s3://{AWS_S3_BUCKET}/models/)에서 내려받아
    정확히 위 경로 상수 자리에 저장한다. Docker 이미지/git 저장소에 150MB짜리 가중치를
    더 이상 baked-in 하지 않기 위한 최초 1회(워커 프로세스당) 다운로드 로직.

    자격증명이 없으면 조용히 폴백하지 않고 명시적으로 예외를 던진다 - 모델이 없으면
    검수 자체가 불가능하므로 목업으로 얼버무릴 수 없는 종류의 실패다. Celery 태스크
    입장에서는 이 예외가 그대로 올라가 재시도(추후 DLQ)로 이어진다.
    """
    missing = [
        p for p in (MODEL_RECALL_PATH, MODEL_PRECISION_PATH, MODEL_DOODLE_PATH, MODEL_BOOK_ROI_PATH)
        if not p.exists()
    ]
    if not missing:
        return

    import boto3
    from app.core.config import settings

    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        raise RuntimeError(
            f"YOLO 모델 가중치가 로컬에 없습니다 ({[p.name for p in missing]}). "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY가 설정되어 있지 않아 S3에서도 내려받을 수 없습니다 "
            "- .env에 자격증명을 설정하세요."
        )

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )

    for path in missing:
        s3_key = f"models/{path.name}"
        print(f"[WBF Detector] {path.name} 로컬에 없음 - s3://{settings.AWS_S3_BUCKET}/{s3_key} 에서 다운로드 중...")
        try:
            s3_client.download_file(settings.AWS_S3_BUCKET, s3_key, str(path))
            print(f"  - {path.name} 다운로드 완료 ({path.stat().st_size / 1024 / 1024:.1f}MB)")
        except Exception as e:
            # ROI 게이트 모델은 없어도 풀프레임 폴백으로 검수가 가능하므로 fail-open.
            # 결함 3모델은 없으면 검수 자체가 불가능하므로 기존대로 fail-hard 유지.
            if path == MODEL_BOOK_ROI_PATH:
                print(f"  [Warning] ROI 게이트 모델 다운로드 실패({e}) - 풀프레임 폴백으로 서빙 계속")
                continue
            raise RuntimeError(f"S3에서 {s3_key} 다운로드 실패: {e}") from e

def calculate_iou(box1: np.ndarray, box2: np.ndarray) -> float:
    """[x1, y1, x2, y2] 포맷의 두 BBox 간 IoU(Intersection over Union) 계산"""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter_area = max(0, x2 - x1) * max(0, y2 - y1)
    if inter_area == 0:
        return 0.0

    box1_area = (box1[2] - box1[0]) * (box1[3] - box1[1])
    box2_area = (box2[2] - box2[0]) * (box2[3] - box2[1])

    union_area = box1_area + box2_area - inter_area
    if union_area == 0:
        return 0.0

    return float(inter_area / union_area)

def weighted_boxes_fusion_pure(
    boxes_list: List[np.ndarray],
    scores_list: List[np.ndarray],
    labels_list: List[np.ndarray],
    weights: List[float] = [1.0, 1.5, 2.0],
    iou_thr: float = 0.5,
    skip_box_thr: float = 0.05,
    selection_mode: str = "precision_first"
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    순수 NumPy WBF 알고리즘 구현 (3대 모델 멀티 앙상블)
    :param boxes_list: [Model1_boxes, Model2_boxes, Model3_doodle_boxes]
    :param scores_list: [Model1_scores, Model2_scores, Model3_scores]
    :param labels_list: [Model1_labels, Model2_labels, Model3_labels]
    :return: (fused_boxes, fused_scores, fused_labels)
    """
    overall_boxes = []
    
    # 1. 모든 모델의 박스를 가중치 스코어로 통합
    for model_idx, (boxes, scores, labels) in enumerate(zip(boxes_list, scores_list, labels_list)):
        weight = weights[model_idx] if model_idx < len(weights) else 1.0
        for box, score, label in zip(boxes, scores, labels):
            if score < skip_box_thr:
                continue
            overall_boxes.append({
                'box': np.array(box, dtype=np.float32),
                'score': float(score),
                'weighted_score': float(score * weight),
                'label': int(label),
                'model_idx': model_idx
            })

    if not overall_boxes:
        return np.empty((0, 4)), np.empty((0,)), np.empty((0,), dtype=int)

    # 스코어 높은 순 정렬
    overall_boxes = sorted(overall_boxes, key=lambda x: x['score'], reverse=True)

    clusters = []  # [{boxes: [], label: int}]
    for b in overall_boxes:
        matched = False
        for c in clusters:
            # 동일 레이블이거나 WBF 융합 대상일 때 IoU 계산
            if c['label'] == b['label']:
                iou = calculate_iou(c['boxes'][0]['box'], b['box'])
                if iou >= iou_thr:
                    c['boxes'].append(b)
                    matched = True
                    break
        if not matched:
            clusters.append({'label': b['label'], 'boxes': [b]})

    fused_boxes = []
    fused_scores = []
    fused_labels = []

    for c in clusters:
        boxes = c['boxes']
        total_weight = sum(weights[b['model_idx']] if b['model_idx'] < len(weights) else 1.0 for b in boxes)
        
        # 가중 평균 박스 계산
        weighted_box = np.zeros(4, dtype=np.float32)
        for b in boxes:
            w = weights[b['model_idx']] if b['model_idx'] < len(weights) else 1.0
            weighted_box += b['box'] * w
        weighted_box /= total_weight

        # 최고 스코어 선택
        max_score = max(b['score'] for b in boxes)

        fused_boxes.append(weighted_box)
        fused_scores.append(max_score)
        fused_labels.append(c['label'])

    return np.array(fused_boxes), np.array(fused_scores), np.array(fused_labels, dtype=int)

class WBFBookDefectDetector:
    """WBF 앙상블 도서 결함 픽셀 추론 엔진 (3-Model Hybrid Ensemble)"""
    def __init__(self):
        print(f"[WBF Detector] Loading 3-Model Ensemble Pipeline...")
        ensure_model_weights()
        self.model_recall = None
        self.model_precision = None
        self.model_doodle = None

        if MODEL_RECALL_PATH.exists():
            self.model_recall = YOLO(str(MODEL_RECALL_PATH))
            print(f"  - Model 1 (High Recall): {MODEL_RECALL_PATH.name} Loaded")
        else:
            print(f"  [Warning] High Recall model missing at {MODEL_RECALL_PATH}")

        if MODEL_PRECISION_PATH.exists():
            self.model_precision = YOLO(str(MODEL_PRECISION_PATH))
            print(f"  - Model 2 (High Precision): {MODEL_PRECISION_PATH.name} Loaded")
        else:
            print(f"  [Warning] High Precision model missing at {MODEL_PRECISION_PATH}")

        if MODEL_DOODLE_PATH.exists():
            self.model_doodle = YOLO(str(MODEL_DOODLE_PATH))
            print(f"  - Model 3 (Stage 2 Doodle OCR): {MODEL_DOODLE_PATH.name} Loaded (mAP50 84.2%)")
        else:
            print(f"  [Warning] Stage 2 Doodle OCR model missing at {MODEL_DOODLE_PATH}")

        # Stage 0: 책 ROI 게이트 (COCO 사전학습, 결함 판정에는 관여하지 않음)
        self.model_roi = None
        if MODEL_BOOK_ROI_PATH.exists():
            self.model_roi = YOLO(str(MODEL_BOOK_ROI_PATH))
            print(f"  - Stage 0 (Book ROI Gate): {MODEL_BOOK_ROI_PATH.name} Loaded (COCO cls 73)")
        else:
            print(f"  [Warning] Book ROI gate model missing at {MODEL_BOOK_ROI_PATH} - 풀프레임 폴백 서빙")

        self.class_names = {0: "Wornout", 1: "ripped", 2: "doodle_scribble"}

    def _detect_book_roi(self, image_bgr: np.ndarray) -> Optional[Tuple[float, float, float, float]]:
        """
        COCO book 클래스로 프레임 내 책 영역을 찾아 정규화 좌표 (x1, y1, x2, y2)를 반환한다.

        - 여러 권이 잡히면 최대 면적 박스 1개만 채택 (검수대에는 검수 대상 1권만 올라온다).
        - 결함이 책 가장자리(모서리 마모 등)에 걸치는 경우가 많으므로 4% 패딩을 준다.
        - ROI가 프레임의 8% 미만이면 오탐 가능성이 높으므로 버리고 풀프레임 폴백(None).
        """
        if self.model_roi is None:
            return None
        try:
            r = self.model_roi(
                image_bgr, conf=0.25, classes=[COCO_BOOK_CLASS_ID], imgsz=640, verbose=False
            )[0]
        except Exception as e:
            print(f"[WBF Detector] ROI 게이트 추론 실패({e}) - 풀프레임 폴백")
            return None

        if len(r.boxes) == 0:
            return None

        xyxyn = r.boxes.xyxyn.cpu().numpy()
        areas = (xyxyn[:, 2] - xyxyn[:, 0]) * (xyxyn[:, 3] - xyxyn[:, 1])
        x1, y1, x2, y2 = xyxyn[int(np.argmax(areas))]

        pad = 0.04
        x1 = max(0.0, float(x1) - pad)
        y1 = max(0.0, float(y1) - pad)
        x2 = min(1.0, float(x2) + pad)
        y2 = min(1.0, float(y2) + pad)

        if (x2 - x1) * (y2 - y1) < 0.08:
            return None
        return (x1, y1, x2, y2)

    def detect_defects_wbf(
        self,
        image_path: str,
        conf_recall: float = 0.12,
        conf_precision: float = 0.25,
        conf_doodle: float = 0.20,
        iou_thr: float = 0.5,
        max_box_area_ratio: float = 0.8,
    ) -> List[Dict[str, Any]]:
        """
        단일 이미지에 대해 [Stage 0 책 ROI 크롭] -> [3대 모델 WBF 앙상블] 결함 추론을
        실행하고 융합된 BBox 리스트를 반환합니다.

        반환 bbox는 ROI 적용 여부와 무관하게 항상 **원본 이미지 기준 0~1000 상대좌표**다.
        (프론트 오버레이는 S3 원본 위에 그려지므로, 크롭 좌표를 원본 좌표로 역변환한다.)
        """
        image_bgr = cv2.imread(image_path)
        if image_bgr is None:
            print(f"[WBF Detector] 이미지 로드 실패: {image_path}")
            return []

        # --- Stage 0: 책 ROI 크롭 (미탐지 시 풀프레임 폴백) ---
        # USE_ROI_GATE=False면 게이트를 통째로 건너뛴다 (상단 상수 주석의 실측 근거 참조).
        roi = self._detect_book_roi(image_bgr) if USE_ROI_GATE else None
        if roi is not None:
            h, w = image_bgr.shape[:2]
            rx1, ry1, rx2, ry2 = roi
            px1, py1 = int(rx1 * w), int(ry1 * h)
            px2, py2 = int(rx2 * w), int(ry2 * h)
            infer_img = image_bgr[py1:py2, px1:px2]
        else:
            infer_img = image_bgr

        boxes_list = []
        scores_list = []
        labels_list = []
        weights = []

        # Model 1 (High Recall) 추론
        if self.model_recall is not None:
            r1 = self.model_recall(infer_img, conf=conf_recall, imgsz=DEFECT_IMGSZ, verbose=False)[0]
            if len(r1.boxes) > 0:
                boxes_list.append(r1.boxes.xyxyn.cpu().numpy())
                scores_list.append(r1.boxes.conf.cpu().numpy())
                labels_list.append(r1.boxes.cls.cpu().numpy().astype(int))
            else:
                boxes_list.append(np.empty((0, 4)))
                scores_list.append(np.empty((0,)))
                labels_list.append(np.empty((0,), dtype=int))
            weights.append(1.0)

        # Model 2 (High Precision) 추론
        if self.model_precision is not None:
            r2 = self.model_precision(infer_img, conf=conf_precision, imgsz=DEFECT_IMGSZ, verbose=False)[0]
            if len(r2.boxes) > 0:
                boxes_list.append(r2.boxes.xyxyn.cpu().numpy())
                scores_list.append(r2.boxes.conf.cpu().numpy())
                labels_list.append(r2.boxes.cls.cpu().numpy().astype(int))
            else:
                boxes_list.append(np.empty((0, 4)))
                scores_list.append(np.empty((0,)))
                labels_list.append(np.empty((0,), dtype=int))
            weights.append(1.5)

        # Model 3 (Stage 2 Doodle OCR - 전담 손글씨/낙서) 추론
        # [2026-08-05] weight 2.0 -> 1.0 하향. AIHub 손글씨 패치 도메인 모델이라 실물 도서
        # 인쇄물(일러스트/본문)에 대한 오탐이 잦은데, 최고 가중치를 주면 그 오탐이 융합
        # 결과를 지배한다. ROI 크롭으로 입력 도메인을 맞춘 뒤에도 인쇄물 오탐은 남으므로
        # 물리 결함 2모델과 동률(1.0)로 재조정.
        if self.model_doodle is not None:
            r3 = self.model_doodle(infer_img, conf=conf_doodle, imgsz=640, verbose=False)[0]
            if len(r3.boxes) > 0:
                boxes_list.append(r3.boxes.xyxyn.cpu().numpy())
                scores_list.append(r3.boxes.conf.cpu().numpy())
                # 클래스 2 (doodle_scribble)로 태깅
                doodle_labels = np.full(len(r3.boxes), 2, dtype=int)
                labels_list.append(doodle_labels)
            else:
                boxes_list.append(np.empty((0, 4)))
                scores_list.append(np.empty((0,)))
                labels_list.append(np.empty((0,), dtype=int))
            weights.append(1.0)

        if not boxes_list:
            return []

        # WBF 융합 연산 실행
        f_boxes, f_scores, f_labels = weighted_boxes_fusion_pure(
            boxes_list,
            scores_list,
            labels_list,
            weights=weights,
            iou_thr=iou_thr
        )

        results = []
        for box, score, label in zip(f_boxes, f_scores, f_labels):
            # 추론 영역(ROI 또는 풀프레임)의 대부분을 덮는 거대 박스는 결함이 아니라
            # 배경/도메인 오탐 신호이므로 폐기한다 (실물 결함이 책의 80%를 덮는 경우는 없다).
            if (box[2] - box[0]) * (box[3] - box[1]) > max_box_area_ratio:
                continue

            # ROI 크롭 좌표 -> 원본 이미지 정규화 좌표 역변환
            if roi is not None:
                rx1, ry1, rx2, ry2 = roi
                rw, rh = (rx2 - rx1), (ry2 - ry1)
                box = np.array([
                    rx1 + box[0] * rw,
                    ry1 + box[1] * rh,
                    rx1 + box[2] * rw,
                    ry1 + box[3] * rh,
                ], dtype=np.float32)

            # 0~1000 정규화 BBox 스케일로 변환
            xmin, ymin, xmax, ymax = (box * 1000).astype(int)
            class_name = self.class_names.get(int(label), "Unknown")

            results.append({
                "defect_type": class_name,
                "confidence": round(float(score), 4),
                "bbox": {
                    "ymin": int(ymin),
                    "xmin": int(xmin),
                    "ymax": int(ymax),
                    "xmax": int(xmax)
                }
            })

        return results

    def detect_doodle_only(
        self,
        image_bgr: np.ndarray,
        conf: float = 0.20,
        max_box_area_ratio: float = 0.8,
        debug_tag: Optional[str] = None,
        debug_meta: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Model 3(doodle) **단독** 추론. WBF 융합을 거치지 않는다.

        [용도 - Track 2·3] VLM이 속지 사진에서 잘라준 크롭을 받아 낙서 위치만 좌표로 찍는다.
        `detect_defects_wbf()`를 쓰면 3모델이 모두 돌고 융합까지 되므로 이 경로에는 맞지 않다.
        doodle 모델은 AIHub 손글씨 크롭 1만 장을 imgsz=640으로 학습해 이 작업에 특화돼 있다.

        Track 1(앞면·뒷면·책등)에서는 표지에 쓴 이름·낙서를 잡기 위해 기존 3모델 앙상블을
        그대로 쓴다. 즉 같은 모델을 두 진입점으로 사용한다.

        Args:
            image_bgr: 크롭된 BGR 이미지 (파일 경로가 아니라 배열을 받는다 - VLM이 지정한
                       영역을 호출부에서 이미 잘라낸 상태이기 때문).
            debug_tag: 지정하면 크롭본과 탐지 결과를 로컬에 남긴다 (SAVE_VLM_CROPS 참조).
                       VLM이 속지를 제대로 잘랐는지는 눈으로 확인해야만 알 수 있으므로,
                       실측 데이터를 쌓아 두고 나중에 검증한다.
            debug_meta: 사이드카 JSON에 함께 기록할 정보 (원본 파일명, VLM이 준 박스,
                        판별된 사진 종류 등).
        Returns:
            `detect_defects_wbf()`와 **동일한 형식**. bbox는 입력 이미지(=크롭본) 기준
            0~1000 정규화 좌표이므로, 원본 좌표가 필요하면 호출부에서 역변환해야 한다.
        """
        if self.model_doodle is None or image_bgr is None or image_bgr.size == 0:
            return []

        try:
            r = self.model_doodle(image_bgr, conf=conf, imgsz=640, verbose=False)[0]
        except Exception as e:
            # 부가 탐지이므로 실패가 파이프라인을 멈추게 하지 않는다 (fail-open).
            print(f"[WBF Detector] doodle 단독 추론 실패({type(e).__name__}) - 낙서 탐지 생략: {e}")
            return []

        if len(r.boxes) == 0:
            return []

        results = []
        xyxyn = r.boxes.xyxyn.cpu().numpy()
        confs = r.boxes.conf.cpu().numpy()
        for box, score in zip(xyxyn, confs):
            # 융합 경로와 동일하게 거대 박스는 오탐 신호로 보고 폐기한다.
            if (box[2] - box[0]) * (box[3] - box[1]) > max_box_area_ratio:
                continue
            xmin, ymin, xmax, ymax = (np.asarray(box) * 1000).astype(int)
            results.append({
                "defect_type": self.class_names.get(2, "doodle_scribble"),
                "confidence": round(float(score), 4),
                "bbox": {
                    "ymin": int(ymin),
                    "xmin": int(xmin),
                    "ymax": int(ymax),
                    "xmax": int(xmax),
                },
            })

        if debug_tag:
            save_vlm_crop(image_bgr, debug_tag, detections=results, meta=debug_meta)
        return results


# 모듈 레벨 싱글턴 - 워커 프로세스당 1회만 3개 YOLO 모델을 로드한다 (요청마다 재로드 금지).
wbf_detector = WBFBookDefectDetector()

if __name__ == "__main__":
    print("[WBF Detector Init Check] Class initialized successfully!")
