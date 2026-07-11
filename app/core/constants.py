from enum import Enum

class BoxStandardEnum(str, Enum):
    BOX_1 = "우체국_1호" 
    BOX_2 = "우체국_2호" 
    BOX_3 = "우체국_3호" 
    BOX_4 = "우체국_4호" 
    BOX_5 = "우체국_5호" 
    BOX_6 = "우체국_6호" 

# 3D Bin Packing 알고리즘을 위한 박스 규격 상수 (mm 단위, 무게는 kg 단위)
# DB 조회 없이 메모리에 상주시켜 연산 속도를 극대화 (기획서 명세 기준)
BOX_STANDARDS = {
    BoxStandardEnum.BOX_1: {"width": 220, "length": 190, "height": 90, "max_weight": 2},
    BoxStandardEnum.BOX_2: {"width": 270, "length": 180, "height": 150, "max_weight": 3},
    BoxStandardEnum.BOX_3: {"width": 340, "length": 250, "height": 210, "max_weight": 5},
    BoxStandardEnum.BOX_4: {"width": 410, "length": 310, "height": 280, "max_weight": 10},
    BoxStandardEnum.BOX_5: {"width": 480, "length": 380, "height": 340, "max_weight": 20},
    BoxStandardEnum.BOX_6: {"width": 520, "length": 480, "height": 400, "max_weight": 30},
}
