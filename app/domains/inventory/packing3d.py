"""
정석 3D Bin Packing 엔진 — Extreme Point 기반 Best-Fit-Decreasing (EP-BFD)

이론 배경:
- Crainic, Perboli, Tadei (2008), "Extreme Point-Based Heuristics for
  Three-Dimensional Bin Packing", INFORMS Journal on Computing.
- 본 구현은 도서 물류 도메인 제약을 반영한 변형:
  1) 회전은 수평 2방향(0°/90°)만 허용 (도서는 눕힘 적재가 원칙 — 표지 손상 방지)
  2) 지지면적(Support Ratio) >= 0.75 안정성 제약 (공중부양/편심 적재 방지)
  3) 박스별 최대 허용 중량(max_weight_kg) 제약
  4) Bottom-Heavy 정렬 (바닥면적 -> 중량 -> 두께 내림차순) = BFD의 Decreasing 기준
  5) 단일 박스 수용 불가 시 First-Fit-Decreasing 다중 박스 분할(Split Shipment)

좌표계: x=박스 length(긴 변), y=박스 width(짧은 변), z=수직 높이 (단위 mm)
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

from app.core.constants import BOX_CATALOG

EPS = 1e-6
SUPPORT_RATIO_MIN = 0.75  # 상부 적재 시 하부 지지면적 최소 비율


@dataclass
class PackItem:
    id: str
    name: str
    length: float   # 도서 긴 변 (mm)
    width: float    # 도서 짧은 변 (mm)
    height: float   # 도서 두께 (mm)
    weight_g: float = 500.0

    @property
    def volume(self) -> float:
        return self.length * self.width * self.height

    @property
    def footprint(self) -> float:
        return self.length * self.width


@dataclass
class Placement:
    item_id: str
    name: str
    x: float
    y: float
    z: float
    length: float   # 배치 후 x축 점유 길이 (회전 반영)
    width: float    # 배치 후 y축 점유 길이 (회전 반영)
    height: float
    rotated: bool   # True = 90도 회전 배치

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "name": self.name,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "z": round(self.z, 1),
            "length": self.length,
            "width": self.width,
            "height": self.height,
            "rotated": self.rotated,
        }


@dataclass
class PackResult:
    box: Dict[str, Any]
    placements: List[Placement] = field(default_factory=list)
    unplaced: List[PackItem] = field(default_factory=list)
    total_weight_g: float = 0.0

    @property
    def all_placed(self) -> bool:
        return len(self.unplaced) == 0

    @property
    def stack_height(self) -> float:
        return max((p.z + p.height for p in self.placements), default=0.0)

    @property
    def volume_fill_ratio(self) -> float:
        box_vol = self.box["length"] * self.box["width"] * self.box["height"]
        used = sum(p.length * p.width * p.height for p in self.placements)
        return round(used / box_vol * 100.0, 1) if box_vol > 0 else 0.0


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _collides(x: float, y: float, z: float, l: float, w: float, h: float,
              placed: List[Placement]) -> bool:
    for p in placed:
        if (
            _overlap_1d(x, x + l, p.x, p.x + p.length) > EPS
            and _overlap_1d(y, y + w, p.y, p.y + p.width) > EPS
            and _overlap_1d(z, z + h, p.z, p.z + p.height) > EPS
        ):
            return True
    return False


def _support_ratio(x: float, y: float, z: float, l: float, w: float,
                   placed: List[Placement]) -> float:
    """z 높이에 놓일 바닥면(l x w)이 기존 적재물 상면으로 지지되는 면적 비율"""
    if z <= EPS:
        return 1.0  # 박스 바닥
    supported = 0.0
    for p in placed:
        if abs((p.z + p.height) - z) <= EPS:
            supported += (
                _overlap_1d(x, x + l, p.x, p.x + p.length)
                * _overlap_1d(y, y + w, p.y, p.y + p.width)
            )
    return supported / (l * w) if l * w > 0 else 0.0


def _prune_eps(eps: List[Tuple[float, float, float]],
               placed: List[Placement],
               box: Dict[str, Any]) -> List[Tuple[float, float, float]]:
    """박스 밖이거나 기존 적재물 내부에 매몰된 Extreme Point 제거 + 중복 제거"""
    result = []
    seen = set()
    for (x, y, z) in eps:
        if x >= box["length"] - EPS or y >= box["width"] - EPS or z >= box["height"] - EPS:
            continue
        inside = False
        for p in placed:
            if (
                p.x - EPS < x < p.x + p.length - EPS
                and p.y - EPS < y < p.y + p.width - EPS
                and p.z - EPS < z < p.z + p.height - EPS
            ):
                inside = True
                break
        if inside:
            continue
        key = (round(x, 3), round(y, 3), round(z, 3))
        if key not in seen:
            seen.add(key)
            result.append((x, y, z))
    return result


def pack_into_box(
    items: List[PackItem],
    box: Dict[str, Any],
    side_margin_mm: float = 0.0,
    top_margin_mm: float = 0.0,
) -> PackResult:
    """
    EP-BFD 코어 루프: Bottom-Heavy 정렬된 아이템을 Extreme Point 후보 중
    (z, y, x) 사전순 최저 위치에 배치한다 (Bottom-Left-Back 원칙).

    side_margin_mm / top_margin_mm: 완충재(측면 래핑/상단 패드)가 점유할 공간을
    사전 차감한 내부 유효 공간에 패킹한다. 배치 좌표는 박스 원점 기준으로 보정 반환.
    """
    inner = {
        "length": box["length"] - 2.0 * side_margin_mm,
        "width": box["width"] - 2.0 * side_margin_mm,
        "height": box["height"] - top_margin_mm,
        "max_weight_kg": box["max_weight_kg"],
    }
    if inner["length"] <= EPS or inner["width"] <= EPS or inner["height"] <= EPS:
        return PackResult(box=box, unplaced=list(items))

    # BFD 'Decreasing': 바닥면적 -> 중량 -> 두께 내림차순 (Bottom-Heavy 안정 적재)
    ordered = sorted(items, key=lambda it: (it.footprint, it.weight_g, it.height), reverse=True)

    result = PackResult(box=box)
    placed: List[Placement] = []
    eps: List[Tuple[float, float, float]] = [(0.0, 0.0, 0.0)]
    max_weight_g = box["max_weight_kg"] * 1000.0

    for item in ordered:
        if result.total_weight_g + item.weight_g > max_weight_g + EPS:
            result.unplaced.append(item)
            continue

        best: Optional[Tuple[Tuple[float, float, float], bool]] = None
        # EP 후보를 (z, y, x) 오름차순으로 스캔 -> 가장 낮고 뒤쪽 위치 우선
        for ep in sorted(eps, key=lambda e: (e[2], e[1], e[0])):
            x, y, z = ep
            for rotated in (False, True):
                l = item.width if rotated else item.length
                w = item.length if rotated else item.width
                if x + l > inner["length"] + EPS or y + w > inner["width"] + EPS:
                    continue
                if z + item.height > inner["height"] + EPS:
                    continue
                if _collides(x, y, z, l, w, item.height, placed):
                    continue
                if _support_ratio(x, y, z, l, w, placed) < SUPPORT_RATIO_MIN:
                    continue
                best = (ep, rotated)
                break
            if best:
                break

        if not best:
            result.unplaced.append(item)
            continue

        (x, y, z), rotated = best
        l = item.width if rotated else item.length
        w = item.length if rotated else item.width
        pl = Placement(
            item_id=item.id, name=item.name,
            x=x, y=y, z=z, length=l, width=w, height=item.height, rotated=rotated,
        )
        placed.append(pl)
        result.total_weight_g += item.weight_g

        # 신규 Extreme Point 3점 생성 (배치 큐보이드의 +x, +y, +z 코너)
        eps.extend([(x + l, y, z), (x, y + w, z), (x, y, z + item.height)])
        eps = _prune_eps(eps, placed, inner)

    # 내부 유효 공간 좌표 -> 박스 원점 기준 좌표 보정 (완충재 측면 두께 오프셋)
    if side_margin_mm > 0.0:
        for p in placed:
            p.x += side_margin_mm
            p.y += side_margin_mm

    result.placements = placed
    return result


def recommend_and_pack(
    items: List[PackItem],
    side_margin_mm: float = 0.0,
    top_margin_mm: float = 0.0,
) -> Dict[str, Any]:
    """
    박스 카탈로그(16종) 부피 오름차순 전수 탐색:
    전량 배치에 성공하는 최소 박스를 선택한다.
    단일 박스 수용 불가 시 마스터 카톤(FFD) 다중 분할 결과를 반환한다.
    """
    sorted_boxes = sorted(BOX_CATALOG, key=lambda b: b["length"] * b["width"] * b["height"])

    for box in sorted_boxes:
        res = pack_into_box(items, box, side_margin_mm, top_margin_mm)
        if res.all_placed:
            return {"split": False, "results": [res], "box_count": 1}

    # 단일 박스 불가 -> 최대 규격(마스터 카톤)으로 First-Fit-Decreasing 분할
    master = sorted_boxes[-1]
    remaining = list(items)
    results: List[PackResult] = []
    while remaining:
        res = pack_into_box(remaining, master, side_margin_mm, top_margin_mm)
        if not res.placements:
            # 단일 아이템이 마스터 카톤조차 초과 (이론상 도서에선 발생 불가) — 무한루프 방어
            res.unplaced = remaining
            results.append(res)
            break
        results.append(res)
        placed_ids = {p.item_id for p in res.placements}
        remaining = [it for it in remaining if it.id not in placed_ids]

    return {"split": True, "results": results, "box_count": len(results)}
