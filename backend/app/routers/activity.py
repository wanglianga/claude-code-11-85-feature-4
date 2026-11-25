"""服务活动时间重建接口：来源画像 / 曲线重建 / 批次对账 / 明细。"""
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ActivityRecord
from ..security import current_user
from ..services import activity as svc

router = APIRouter(prefix="/api/activity", tags=["activity"],
                   dependencies=[Depends(current_user)])


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    return date.fromisoformat(d)


@router.get("/profiles")
def profiles(db: Session = Depends(get_db)):
    return {"items": svc.profile_feedback(db)}


@router.get("/reconstruct")
def reconstruct(org: str = "ALL", kind: str = Query("visit", pattern="^(visit|settlement)$"),
                start: str | None = None, end: str | None = None,
                db: Session = Depends(get_db)):
    return svc.reconstruct(db, org, kind, _parse(start), _parse(end))


@router.get("/batches")
def batches(org: str | None = None, db: Session = Depends(get_db)):
    rows = svc.load_batches(db, org)
    return {"items": [
        {"batch_no": b.batch_no, "org_code": b.org_code,
         "signed_at": b.signed_at.isoformat() if b.signed_at else None,
         "seq_count": b.seq_count, "declared_units": b.declared_units,
         "metadata_complete": b.metadata_complete,
         "service_date_count": len(b.service_dates or {}),
         "service_dates": b.service_dates or {}, "note": b.note}
        for b in rows]}


@router.get("/records")
def records(org: str | None = None, kind: str | None = None,
            unknown_only: bool = False, limit: int = Query(300, le=1000),
            db: Session = Depends(get_db)):
    q = db.query(ActivityRecord)
    if org and org != "ALL":
        q = q.filter(ActivityRecord.org_code == org)
    if kind:
        q = q.filter(ActivityRecord.kind == kind)
    if unknown_only:
        q = q.filter(ActivityRecord.service_date.is_(None))
    rows = q.order_by(ActivityRecord.signed_at, ActivityRecord.id).limit(limit).all()
    return {"items": [
        {"code": r.code, "org_code": r.org_code, "kind": r.kind,
         "batch_no": r.batch_no, "batch_seq": r.batch_seq,
         "service_date": r.service_date.isoformat() if r.service_date else None,
         "signed_at": r.signed_at.isoformat() if r.signed_at else None,
         "unit_count": r.unit_count,
         "amount": float(r.amount) if r.amount is not None else None}
        for r in rows]}
