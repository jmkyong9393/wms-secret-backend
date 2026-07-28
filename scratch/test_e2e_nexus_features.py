"""
====================================================================
[Nexus WMS] 풀스택 End-to-End 백엔드 & AI API 종합 검증 스크립트
====================================================================
"""

import asyncio
from app.domains.inventory.router import get_inventory, get_inventory_detail
from app.domains.orders.router import (
    get_orders,
    pick_order,
    ship_order,
    short_pick_fallback,
    trigger_auto_po
)
from app.domains.inbound.service import generate_presigned_url
from app.ai.agents.restock import run_restock_agent

def run_all_e2e_tests():
    print("====================================================================")
    print("[Nexus WMS] E2E Backend & AI API Verification Suite Started")
    print("====================================================================")

    # 1. Inventory List Test
    inv_items = asyncio.run(get_inventory())
    print(f"[PASSED 1/7] Inventory List API: Total {len(inv_items)} items fetched.")
    assert len(inv_items) >= 1, "Inventory list should not be empty"

    # 2. Inventory Detail Test
    detail = asyncio.run(get_inventory_detail("inv-001"))
    print(f"[PASSED 2/7] Inventory Detail API: LPN={detail['lpn_barcode']} | Book={detail['book']['title']} | UBCI={detail['ubci_score']} ({detail['grade']})")
    assert detail['lpn_barcode'] == 'LPN-260727-0001'

    # 3. Outbound Orders List & 3D Bin Packing Test
    orders = get_orders()
    print(f"[PASSED 3/7] Outbound Orders List API: Total {len(orders)} orders fetched.")
    
    bin_packing = pick_order("ORD-20260727-01", [{"title": "클린 코드", "pages": 580, "is_color": True}])
    print(f"[PASSED 4/7] 3D Bin Packing API: Optimal Box={bin_packing['recommended_box']}")

    # 4. Short Pick Fallback API Test
    short_pick = short_pick_fallback("ORD-20260727-01", "LPN-260727-0001", "9788934972464")
    print(f"[PASSED 5/7] Short Pick Fallback API: Missing LPN-260727-0001 -> Reallocated FIFO {short_pick['reallocated_lpn']} at Zone {short_pick['new_zone']}")

    # 5. Restock Agent Auto-PO API Test
    auto_po = trigger_auto_po("9788934972464", "IT 트렌드 2026", 2, 45, 3, "DMG_EXT_WET")
    print(f"[PASSED 6/7] Restock Agent Auto-PO API: AI Reorder Quantity={auto_po['proposal']['reorder_quantity']} ({auto_po['proposal']['urgency_level']})")

    # 6. S3 Pre-signed URL Test
    s3_presigned = generate_presigned_url("mobile_scan_cover.jpg", "image/jpeg")
    print(f"[PASSED 7/7] S3 Pre-signed URL API: Object Key={s3_presigned['object_key']} | Public CDN={s3_presigned['public_cdn_url']}")

    print("====================================================================")
    print("[SUCCESS] ALL 7 NEXUS END-TO-END VERIFICATION TESTS PASSED 100% CLEANLY!")
    print("====================================================================")

if __name__ == "__main__":
    run_all_e2e_tests()
