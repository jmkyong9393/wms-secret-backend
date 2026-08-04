from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
from sqlmodel import Session
from app.db.session import get_db
from app.domains.po.service import po_service

router = APIRouter(prefix="/po", tags=["Auto PO"])

class ApproveRequest(BaseModel):
    book_ids: List[str]

class CancelRequest(BaseModel):
    book_ids: List[str]

class DeductStockRequest(BaseModel):
    book_id: str
    deduct_qty: int = 10
    reason: str = "출고/파손 폐기 차감"


@router.get("/suggested")
def get_suggested_po(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    return po_service.get_suggested_po(db)


@router.post("/deduct")
def deduct_stock_simulation(req: DeductStockRequest, db: Session = Depends(get_db)):
    return po_service.deduct_stock_simulation(db, req.book_id, req.deduct_qty)


@router.post("/approve")
def approve_po(req: ApproveRequest, db: Session = Depends(get_db)):
    return po_service.approve_po(db, req.book_ids)


@router.post("/cancel")
def cancel_po(req: CancelRequest, db: Session = Depends(get_db)):
    return po_service.cancel_po(req.book_ids)
