"""
====================================================================
[WMS B2B Vision AI - WBF (Weighted Boxes Fusion) 앙상블 탐지 모듈]
- Stage 0: yolov8n_coco_roi.pt (COCO book cls=73) -> 책 ROI 크롭 게이트
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
====================================================================
"""

import os
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
        roi = self._detect_book_roi(image_bgr)
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
            r1 = self.model_recall(infer_img, conf=conf_recall, imgsz=800, verbose=False)[0]
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
            r2 = self.model_precision(infer_img, conf=conf_precision, imgsz=800, verbose=False)[0]
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

# 모듈 레벨 싱글턴 - 워커 프로세스당 1회만 3개 YOLO 모델을 로드한다 (요청마다 재로드 금지).
wbf_detector = WBFBookDefectDetector()

if __name__ == "__main__":
    print("[WBF Detector Init Check] Class initialized successfully!")
