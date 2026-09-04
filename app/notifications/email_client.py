import os
import email
import asyncio
import base64
from email.message import EmailMessage
from email.utils import make_msgid

from app.notifications.gmail_auth import get_gmail_service

def _format_details(details: dict) -> str:
    return "\n".join(f"  {key}: {value}" for key, value in details.items())

def _get_real_message_id_sync(service, gmail_message_id: str) -> str:
    result = service.users().messages().get(
        userId = "me",
        id = gmail_message_id,
        format = "raw"
    ).execute()
    raw_bytes = base64.urlsafe_b64decode(result["raw"])
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    message_id = msg.get("Message-ID")
    if not message_id:
        raise RuntimeError(
            f"Sent message {gmail_message_id} has no Message-ID header on "
            f"readback — unexpected, Gmail should always assign one. Cannot "
            f"correlate replies without it."
        )
    return message_id

def _build_and_send_sync(interrupt_payload: dict, thread_id: str, reviewer_email: str) -> str:
    action_type = interrupt_payload["action_type"]
    agent = interrupt_payload["agent"]
    risk_level = interrupt_payload["risk_level"]
    details = interrupt_payload["details"]
 
    msg = EmailMessage()
    msg["Message-ID"] = make_msgid(domain="customer-support-bot.local")
    msg["To"] = reviewer_email
    msg["Subject"] = f"[Approval needed] {action_type} ({risk_level} risk) — thread {thread_id}"
    msg.set_content(
        f"An automated agent is requesting approval for an action.\n\n"
        f"Agent:        {agent}\n"
        f"Action type:  {action_type}\n"
        f"Risk level:   {risk_level}\n"
        f"Thread:       {thread_id}\n\n"
        f"Details:\n{_format_details(details)}\n\n"
        f"------------------------------------------------------------\n"
        f"To respond, reply to this email with APPROVE or REJECT as the\n"
        f"very FIRST WORD of your reply, above any quoted text.\n"
        f"Anything else in your reply is ignored for parsing purposes.\n"
    )
 
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    send_response = service.users().messages().send(userId="me", body={"raw": raw}).execute()
 
    return _get_real_message_id_sync(service, send_response["id"])

async def send_approval_request_email(interrupt_payload: dict, thread_id: str) -> str:
    """
    Returns the Message-ID that was set (angle brackets included) — the
    caller MUST persist this via approval_log.set_outbound_message_id()
    or reply correlation breaks. Unchanged contract from the SMTP version.
 
    Runs the actual send in a thread since google-api-python-client is
    synchronous — same treatment imaplib's blocking calls got before.
    """
    reviewer_email = os.environ["REVIEWER_EMAIL"]
    return await asyncio.to_thread(_build_and_send_sync, interrupt_payload, thread_id, reviewer_email)