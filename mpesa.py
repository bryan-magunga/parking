"""Small Daraja M-Pesa Express (STK Push) client using only stdlib."""

import base64
import json
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MpesaError(RuntimeError):
    pass


def _setting(name, default=None):
    return os.environ.get(name, default)


def configured():
    return all(_setting(key) for key in (
        "MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE",
        "MPESA_PASSKEY", "MPESA_CALLBACK_URL",
    ))


def _request(url, payload=None, authorization=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    request = Request(url, data=body, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise MpesaError("Could not reach M-Pesa. Please try again.") from exc


def _base_url():
    environment = _setting("MPESA_ENVIRONMENT", "sandbox").lower()
    if environment not in ("sandbox", "production"):
        raise MpesaError("MPESA_ENVIRONMENT must be sandbox or production.")
    host = "sandbox.safaricom.co.ke" if environment == "sandbox" else "api.safaricom.co.ke"
    return f"https://{host}"


def _access_token():
    credentials = base64.b64encode(
        f"{_setting('MPESA_CONSUMER_KEY')}:{_setting('MPESA_CONSUMER_SECRET')}".encode()
    ).decode()
    result = _request(f"{_base_url()}/oauth/v1/generate?grant_type=client_credentials", authorization=f"Basic {credentials}")
    token = result.get("access_token")
    if not token:
        raise MpesaError("M-Pesa authorization failed. Check the Daraja credentials.")
    return token


def normalize_phone(phone):
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") or digits.startswith("1"):
        digits = "254" + digits
    if len(digits) != 12 or not (digits.startswith("2547") or digits.startswith("2541")):
        raise MpesaError("Enter a valid Kenyan M-Pesa number, for example 0712345678.")
    return digits


def initiate_stk_push(phone, amount, account_reference):
    if not configured():
        raise MpesaError("M-Pesa is not configured yet. Add the Daraja settings to the environment.")
    shortcode = _setting("MPESA_SHORTCODE")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{shortcode}{_setting('MPESA_PASSKEY')}{timestamp}".encode()
    ).decode()
    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": _setting("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline"),
        "Amount": int(amount),
        "PartyA": normalize_phone(phone),
        "PartyB": shortcode,
        "PhoneNumber": normalize_phone(phone),
        "CallBackURL": _setting("MPESA_CALLBACK_URL"),
        "AccountReference": str(account_reference)[:12],
        "TransactionDesc": "Parking fee",
    }
    result = _request(
        f"{_base_url()}/mpesa/stkpush/v1/processrequest", payload,
        authorization=f"Bearer {_access_token()}",
    )
    checkout_id = result.get("CheckoutRequestID")
    if not checkout_id:
        raise MpesaError(result.get("errorMessage") or result.get("ResponseDescription") or "M-Pesa did not accept the payment request.")
    return result


def stk_push_succeeded(checkout_request_id):
    """Confirm a callback against Daraja before treating it as paid."""
    if not configured():
        return False
    shortcode = _setting("MPESA_SHORTCODE")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(
        f"{shortcode}{_setting('MPESA_PASSKEY')}{timestamp}".encode()
    ).decode()
    result = _request(
        f"{_base_url()}/mpesa/stkpushquery/v1/query",
        {
            "BusinessShortCode": shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        },
        authorization=f"Bearer {_access_token()}",
    )
    return str(result.get("ResultCode")) == "0"
