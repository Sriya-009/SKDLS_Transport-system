import os

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID', '').strip()
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN', '').strip()
TWILIO_WHATSAPP_FROM_NUMBER = os.getenv('TWILIO_WHATSAPP_FROM_NUMBER', '').strip()
TWILIO_WHATSAPP_ENABLED = bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM_NUMBER)
TWILIO_MESSAGES_ENDPOINT = (
    f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json'
    if TWILIO_ACCOUNT_SID
    else ''
)


def normalize_whatsapp_number(phone_number):
    """Convert a phone number into the Twilio WhatsApp format."""
    text = str(phone_number or '').strip()
    if not text:
        raise ValueError('Phone number is required')

    if text.startswith('whatsapp:'):
        text = text.replace('whatsapp:', '', 1)

    if text.startswith('+'):
        digits = text[1:]
    else:
        digits = ''.join(ch for ch in text if ch.isdigit())

    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        raise ValueError('Enter a valid phone number with 7 to 15 digits')

    return f'whatsapp:+{digits}'


def is_whatsapp_configured():
    return TWILIO_WHATSAPP_ENABLED


def send_whatsapp_message(phone_number, message):
    """Send a WhatsApp message through Twilio.

    Returns the Twilio response JSON when successful. Raises RuntimeError if the
    integration is not configured or if Twilio returns an error.
    """
    if not TWILIO_WHATSAPP_ENABLED:
        raise RuntimeError('Twilio WhatsApp is not configured')

    recipient = normalize_whatsapp_number(phone_number)
    payload = {
        'From': f'whatsapp:{TWILIO_WHATSAPP_FROM_NUMBER}',
        'To': recipient,
        'Body': str(message or '').strip(),
    }

    if not payload['Body']:
        raise ValueError('Message body is required')

    response = requests.post(
        TWILIO_MESSAGES_ENDPOINT,
        data=payload,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=20,
    )

    if not response.ok:
        raise RuntimeError(f'Twilio WhatsApp send failed: {response.text}')

    return response.json()


def build_booking_confirmation_message(booking):
    return (
        f"SKDLS Transportations booking confirmed.\n"
        f"Booking ID: #{booking.get('id')}\n"
        f"Route: {booking.get('pickup_location') or booking.get('source_location') or 'Pickup pending'}"
        f" -> {booking.get('drop_location') or booking.get('destination_location') or 'Drop pending'}\n"
        f"Truck: {booking.get('truck_type') or booking.get('tyre_type') or 'Pending'}\n"
        f"Status: {booking.get('booking_status') or 'pending'}"
    )


def build_payment_confirmation_message(booking, payment):
    return (
        f"SKDLS Transportations payment confirmed.\n"
        f"Booking ID: #{booking.get('id')}\n"
        f"Payment ID: {payment.get('razorpay_payment_id') or 'Pending'}\n"
        f"Order ID: {payment.get('razorpay_order_id') or 'Pending'}\n"
        f"Amount: ₹{int(payment.get('amount') or booking.get('token_amount') or 0):,}\n"
        f"Status: {payment.get('payment_status') or 'paid'}"
    )


def build_live_location_message(booking, tracking_data):
    latitude = tracking_data.get('latitude')
    longitude = tracking_data.get('longitude')
    last_updated = tracking_data.get('last_updated') or 'recently'
    return (
        f"SKDLS Transportations live truck update.\n"
        f"Booking ID: #{booking.get('id')}\n"
        f"Truck: {tracking_data.get('lorry_number') or booking.get('lorry_number') or 'Pending'}\n"
        f"Current location: {latitude}, {longitude}\n"
        f"Updated: {last_updated}"
    )


def build_delivery_update_message(booking, status_text, details=None):
    suffix = f"\n{details}" if details else ''
    return (
        f"SKDLS Transportations delivery update.\n"
        f"Booking ID: #{booking.get('id')}\n"
        f"Status: {status_text}\n"
        f"Route: {booking.get('pickup_location') or booking.get('source_location') or 'Pickup pending'}"
        f" -> {booking.get('drop_location') or booking.get('destination_location') or 'Drop pending'}"
        f"{suffix}"
    )
