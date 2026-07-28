"""
====================================================================
[Nexus WMS] 데모 시연용 1초/건 가상 출고 주문 무한 생성 데몬 스크립트
- B2B/B2C 가상 출고 주문을 백엔드로 자동 생성하여 실시간 대시보드 시뮬레이션 환경 구축
====================================================================
"""

import time
import random
import requests
from datetime import datetime

BACKEND_URL = "http://localhost:8000/api/v1/orders"

CUSTOMERS = [
    ("교보문고 B2B 광화문점", "WHOLESALE"),
    ("알라딘 중고 온라인몰", "RETAIL"),
    ("YES24 물류센터", "WHOLESALE"),
    ("영풍문고 강남점", "RETAIL"),
    ("개인 구매자 (B2C)", "RETAIL"),
]

CATEGORIES = [
    ("Novel", 22000, 78, 120),
    ("IT", 33000, 95, 30),
    ("Science", 28000, 88, 45),
    ("Economics", 25000, 82, 60),
    ("Comic", 15000, 90, 15),
]

def seed_one_mock_order():
    cust_name, cust_type = random.choice(CUSTOMERS)
    cat, price, ubci, days = random.choice(CATEGORIES)

    params = {
        "customer_name": cust_name,
        "type": cust_type,
        "list_price": price,
        "category": cat,
        "ubci_score": ubci,
        "days_in_inventory": days,
    }

    try:
        res = requests.post(BACKEND_URL, params=params, timeout=3)
        if res.status_code == 201:
            data = res.json()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 🚚 Mock Order Generated: {data['order_id']} | Cust: {cust_name} | Price: ₩{int(data['final_price']):,} ({data['applied_discount_rate']} Off)")
        else:
            print(f"[ERR] Order creation failed: {res.status_code}")
    except Exception as e:
        print(f"[WARN] Backend connection error: {e}")

if __name__ == "__main__":
    print("==========================================================")
    print("🚀 Nexus WMS Mock Order Seeding Daemon Started!")
    print("   Generates 1 random mock order every 3 seconds...")
    print("   Press Ctrl+C to stop daemon.")
    print("==========================================================")
    
    try:
        while True:
            seed_one_mock_order()
            time.sleep(3)
    except KeyboardInterrupt:
        print("\n[STOP] Mock Order Seeding Daemon Terminated.")
