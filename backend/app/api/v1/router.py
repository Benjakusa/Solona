from fastapi import APIRouter

from app.api.v1 import auth, payments, pharmacy, sync, tenants

router = APIRouter()
router.include_router(auth.router)
router.include_router(tenants.router)
router.include_router(pharmacy.router)
router.include_router(sync.router)
router.include_router(payments.billing_router)
router.include_router(payments.platform_router)
