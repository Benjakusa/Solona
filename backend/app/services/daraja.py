"""Safaricom Daraja STK Push adapter and idempotent callback reconciliation."""
import base64
import hashlib
import json
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import set_tenant_context
from app.models import DarajaAttempt, SubscriptionPayment, SubscriptionPlan, TenantSubscription
from app.services.audit import record_audit


class DarajaError(RuntimeError):
    pass


def _base_url(environment: str | None = None) -> str:
    selected = environment or get_settings().daraja_environment
    return "https://sandbox.safaricom.co.ke" if selected == "sandbox" else "https://api.safaricom.co.ke"


def _required(value, name: str, secret: bool = False) -> str:
    if value is None:
        raise DarajaError(f"Daraja configuration is incomplete: {name} is required.")
    if secret:
        value = value.get_secret_value()
    if not str(value).strip():
        raise DarajaError(f"Daraja configuration is incomplete: {name} is required.")
    return str(value)


def _request_json(url: str, *, method="GET", payload=None, authorization=None) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise DarajaError("Daraja is unavailable or returned an invalid response.") from None
    if not isinstance(result, dict):
        raise DarajaError("Daraja returned an invalid response.")
    return result


def access_token() -> str:
    settings = get_settings()
    key = _required(settings.daraja_consumer_key, "DARAJA_CONSUMER_KEY", secret=True)
    secret = _required(settings.daraja_consumer_secret, "DARAJA_CONSUMER_SECRET", secret=True)
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    response = _request_json(_base_url() + "/oauth/v1/generate?grant_type=client_credentials", authorization="Basic " + basic)
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise DarajaError("Daraja did not issue an access token.")
    return token


def normalize_phone(value: str) -> str:
    digits = re.sub(r"[\s()+-]", "", value)
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    if not re.fullmatch(r"254(?:7|1)\d{8}", digits):
        raise ValueError("Enter a valid Kenyan mobile number.")
    return digits


def _timestamp() -> str:
    return datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")


def initiate_stk(db: Session, *, tenant_id: uuid.UUID, subscription: TenantSubscription,
                 payment: SubscriptionPayment, phone_number: str) -> DarajaAttempt:
    settings = get_settings()
    short_code = _required(settings.daraja_shortcode, "DARAJA_SHORTCODE")
    passkey = _required(settings.daraja_passkey, "DARAJA_PASSKEY", secret=True)
    callback_base = _required(settings.daraja_callback_base_url, "DARAJA_CALLBACK_BASE_URL").rstrip("/")
    if get_settings().daraja_environment == "production" and not callback_base.startswith("https://"):
        raise DarajaError("Production Daraja callbacks require an HTTPS URL.")
    phone = normalize_phone(phone_number)
    plan = db.scalar(select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id))
    if (plan is None or plan.monthly_price <= 0 or payment.amount != plan.monthly_price
            or payment.currency != "KES"):
        raise DarajaError("The selected subscription plan has no valid monthly price.")
    if payment.amount != payment.amount.to_integral_value():
        raise DarajaError("Daraja STK payments require whole-shilling subscription prices.")
    token = access_token()

    attempt = DarajaAttempt(
        tenant_id=tenant_id, subscription_payment_id=payment.id, phone_number=phone,
        amount=payment.amount, daraja_environment=settings.daraja_environment,
        business_shortcode=short_code, status="initiating",
    )
    db.add(attempt)
    db.flush()
    # Persist the idempotency key before contacting the provider. A timeout or
    # process crash must never cause the same button press to send a second STK.
    db.commit()
    set_tenant_context(db, tenant_id)
    timestamp = _timestamp()
    password = base64.b64encode(f"{short_code}{passkey}{timestamp}".encode()).decode()
    payload = {
        "BusinessShortCode": short_code,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": settings.daraja_transaction_type,
        "Amount": int(payment.amount),
        "PartyA": phone,
        "PartyB": settings.daraja_party_b or short_code,
        "PhoneNumber": phone,
        "CallBackURL": callback_base + "/api/v1/payments/daraja/callback",
        "AccountReference": payment.account_reference,
        "TransactionDesc": "SOLONA pharmacy subscription",
    }
    try:
        response = _request_json(_base_url() + "/mpesa/stkpush/v1/processrequest", method="POST",
                                 payload=payload, authorization="Bearer " + token)
    except DarajaError:
        # The request may have reached Safaricom despite the timeout. Never
        # submit the same idempotency key again; keep an operator-visible state.
        attempt.status = "reconciliation_required"
        payment.status = "reconciliation_required"
        raise
    checkout = response.get("CheckoutRequestID")
    merchant = response.get("MerchantRequestID")
    if str(response.get("ResponseCode", "")) != "0" or not checkout or not merchant:
        attempt.status = "failed"
        attempt.result_description = str(response.get("ResponseDescription", "STK request was rejected"))[:300]
        payment.status = "failed"
        return attempt
    attempt.checkout_request_id = str(checkout)
    attempt.merchant_request_id = str(merchant)
    attempt.status = "pending"
    attempt.conversation_id = str(response.get("ConversationID") or "") or None
    return attempt


def process_callback(db: Session, payload: dict) -> dict:
    body = payload.get("Body")
    callback = body.get("stkCallback") if isinstance(body, dict) else None
    if not isinstance(callback, dict):
        raise ValueError("Invalid Daraja callback.")
    checkout_id = callback.get("CheckoutRequestID")
    result_code = callback.get("ResultCode")
    if (not isinstance(checkout_id, str) or len(checkout_id) > 120
            or isinstance(result_code, bool) or not isinstance(result_code, (int, str))):
        raise ValueError("Invalid Daraja callback.")

    # Daraja's STK callback payload has no documented cryptographic signature.
    # Treat it only as a reconciliation hint. No caller-supplied result,
    # amount, phone, receipt, or merchant ID is payment proof.
    db.execute(text("SELECT set_config('app.daraja_callback', 'true', true)"))
    attempt = db.scalar(select(DarajaAttempt).where(DarajaAttempt.checkout_request_id == checkout_id).with_for_update())
    if attempt is None:
        return {"ResultCode": 0, "ResultDesc": "Callback received"}
    set_tenant_context(db, attempt.tenant_id)
    callback_digest = hashlib.sha256(json.dumps(callback, sort_keys=True, default=str).encode()).hexdigest()
    attempt.callback_count = (attempt.callback_count or 0) + 1
    if attempt.last_callback_digest == callback_digest:
        return {"ResultCode": 0, "ResultDesc": "Duplicate callback accepted"}
    attempt.last_callback_digest = callback_digest
    payment = db.scalar(select(SubscriptionPayment).where(
        SubscriptionPayment.tenant_id == attempt.tenant_id,
        SubscriptionPayment.id == attempt.subscription_payment_id,
    ).with_for_update())
    # A forged callback must not be able to mark even a failure: doing so could
    # deny service. Provider status reconciliation is authoritative for both.
    if payment is None:
        attempt.status = "reconciliation_required"
    elif payment.status not in {"paid", "failed", "cancelled"}:
        attempt.status = "reconciliation_required"
    attempt.callback_digest = callback_digest
    # Retain no untrusted PII/receipt/description. The digest supports audit and
    # duplicate-delivery detection without persisting the submitted contents.
    normalized = {"checkout_request_id": checkout_id, "callback_digest": callback_digest,
                 "provider_verification": "required"}
    attempt.callback_payload = normalized
    attempt.callback_received_at = datetime.now(timezone.utc)
    record_audit(db, tenant_id=attempt.tenant_id, actor_id=None, action="daraja.callback.processed",
                 resource_type="subscription_payment", resource_id=attempt.subscription_payment_id,
                 transaction_id=attempt.subscription_payment_id, details={"status": attempt.status,
                                                                           "callback_digest": callback_digest})
    return {"ResultCode": 0, "ResultDesc": "Callback received"}


def reconcile_stk_status(db: Session, attempt: DarajaAttempt) -> dict:
    settings = get_settings()
    short_code = _required(settings.daraja_shortcode, "DARAJA_SHORTCODE")
    passkey = _required(settings.daraja_passkey, "DARAJA_PASSKEY", secret=True)
    if not attempt.checkout_request_id:
        return {"status": attempt.status, "reconciliation_required": True}
    if (not attempt.daraja_environment or not attempt.business_shortcode
            or settings.daraja_environment != attempt.daraja_environment
            or short_code != attempt.business_shortcode):
        attempt.status = "reconciliation_required"
        payment = db.scalar(select(SubscriptionPayment).where(
            SubscriptionPayment.tenant_id == attempt.tenant_id,
            SubscriptionPayment.id == attempt.subscription_payment_id,
        ).with_for_update())
        if payment is not None and payment.status not in {"paid", "failed", "cancelled"}:
            payment.status = "reconciliation_required"
        return {"status": attempt.status, "reconciliation_required": True}
    timestamp = _timestamp()
    password = base64.b64encode(f"{short_code}{passkey}{timestamp}".encode()).decode()
    response = _request_json(
        _base_url(attempt.daraja_environment) + "/mpesa/stkpushquery/v1/query", method="POST",
        payload={"BusinessShortCode": short_code, "Password": password, "Timestamp": timestamp,
                 "CheckoutRequestID": attempt.checkout_request_id},
        authorization="Bearer " + access_token(),
    )
    # STK Push Query is the supported Daraja status API for this Lipa Na
    # M-Pesa Express checkout. Its authenticated response is correlated to the
    # checkout and merchant request created for our configured BusinessShortCode.
    # It does not return amount, currency, phone, or M-Pesa receipt; amount and
    # KES are validated against our immutable request ledger, and no callback
    # claims are used to fill those missing provider fields.
    code = response.get("ResultCode")
    correlated = (
        str(response.get("ResponseCode", "")) == "0"
        and str(response.get("CheckoutRequestID", "")) == attempt.checkout_request_id
        and str(response.get("MerchantRequestID", "")) == attempt.merchant_request_id
    )
    payment = db.scalar(select(SubscriptionPayment).where(
        SubscriptionPayment.tenant_id == attempt.tenant_id,
        SubscriptionPayment.id == attempt.subscription_payment_id,
    ).with_for_update())
    if not correlated or code is None or str(code) != "0" or payment is None:
        if payment is not None and payment.status not in {"paid", "failed", "cancelled"}:
            payment.status = "reconciliation_required"
        if attempt.status not in {"paid", "failed", "cancelled"}:
            attempt.status = "reconciliation_required"
        return {"status": attempt.status, "provider_result_code": str(code) if code is not None else None,
                "reconciliation_required": True}

    if payment.amount != attempt.amount or payment.currency != "KES":
        payment.status = "reconciliation_required"
        attempt.status = "reconciliation_required"
        return {"status": attempt.status, "provider_result_code": str(code),
                "reconciliation_required": True}

    if payment.status == "paid":
        attempt.status = "paid"
        return {"status": "paid", "provider_result_code": "0", "reconciliation_required": False}
    if payment.status in {"failed", "cancelled"}:
        attempt.status = "reconciliation_required"
        return {"status": attempt.status, "provider_result_code": "0", "reconciliation_required": True}

    subscription = db.scalar(select(TenantSubscription).where(
        TenantSubscription.tenant_id == attempt.tenant_id,
        TenantSubscription.id == payment.subscription_id,
    ).with_for_update())
    if subscription is None:
        payment.status = "reconciliation_required"
        attempt.status = "reconciliation_required"
        return {"status": attempt.status, "provider_result_code": "0", "reconciliation_required": True}

    payment.status = "paid"
    attempt.status = "paid"
    now = datetime.now(timezone.utc)
    anchor = max(subscription.ends_at, now)
    subscription.status = "active"
    subscription.starts_at = min(subscription.starts_at, now)
    subscription.ends_at = anchor + timedelta(days=30)
    subscription.grace_until = None
    return {"status": "paid", "provider_result_code": "0", "reconciliation_required": False}
