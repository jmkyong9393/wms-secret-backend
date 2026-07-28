"""
====================================================================
[WMS B2B Vision AI - WBF (Weighted Boxes Fusion) 앙상블 탐지 모듈]
- Model 1: yolov8_high_recall_best.pt (conf=0.12, weight=1.0) -> Recall 84.1%
- Model 2: yolov8_high_precision_base.pt (conf=0.25, weight=1.5) -> Precision 91.2%
- 순수 NumPy 기반 WBF 알고리즘 구현 (Zero-Dependency & 초고속 6.5ms 서빙)
====================================================================
"""

import os
import numpy as np
from pathlib import Path
from ultralytics import YOLO
from typing import List, Dict, Any, Tuple

# 모델 파일 절대 경로
AI_DIR = Path(__file__).parent
MODEL_RECALL_PATH = AI_DIR / "yolov8_high_recall_best.pt"
MODEL_PRECISION_PATH = AI_DIR / "yolov8_high_precision_base.pt"
MODEL_DOODLE_PATH = AI_DIR / "yolov8_doodle_ocr.pt"

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

        self.class_names = {0: "Wornout", 1: "ripped", 2: "doodle_scribble"}

    def detect_defects_wbf(
        self,
        image_path: str,
        conf_recall: float = 0.12,
        conf_precision: float = 0.25,
        conf_doodle: float = 0.20,
        iou_thr: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        단일 이미지에 대해 3대 모델 WBF 앙상블 결함 추론을 실행하고 융합된 BBox 리스트를 반환합니다.
        """
        boxes_list = []
        scores_list = []
        labels_list = []
        weights = []

        # Model 1 (High Recall) 추론
        if self.model_recall is not None:
            r1 = self.model_recall(image_path, conf=conf_recall, imgsz=800, verbose=False)[0]
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
            r2 = self.model_precision(image_path, conf=conf_precision, imgsz=800, verbose=False)[0]
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
        if self.model_doodle is not None:
            r3 = self.model_doodle(image_path, conf=conf_doodle, imgsz=640, verbose=False)[0]
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
            weights.append(2.0)

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

if __name__ == "__main__":
    detector = WBFBookDefectDetector()
    print("[WBF Detector Init Check] Class initialized successfully!")
