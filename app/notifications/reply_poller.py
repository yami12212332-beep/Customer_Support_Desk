import asyncio
import base64
import email
import email.policy
import logging
import os
from email.utils import parseaddr

from app.db import approval_log
from app.notifications.decision_parser import extract_plain_text, parse_decision
from app.notifications.gmail_auth import get_gmail_service

logger = logging.getLogger("reply_poller")

_LABEL_NAME = "CS-Bot-Processed"
_label_id_cache: str | None = None


def _is_from_reviewer(msg: email.message.Message) -> bool:
    _, from_addr = parseaddr(msg.get("From", ""))
    reviewer_email = os.environ["REVIEWER_EMAIL"]
    return from_addr.strip().lower() == reviewer_email.strip().lower()


def _extract_candidate_message_ids(msg: email.message.Message) -> list[str]:
    candidates: list[str] = []
    in_reply_to = msg.get("In-Reply-To")
    if in_reply_to:
        candidates.append(in_reply_to.strip())
    references = msg.get("References")
    if references:
        candidates.extend(ref.strip() for ref in references.split() if ref.strip())
    return candidates


def _get_or_create_label_id_sync(service) -> str:
    """
    Cached after first lookup/creation — avoids a labels.list() call every
    single poll cycle. Safe to cache module-wide: label IDs don't change
    once created, and there's only ever one Gmail account per process here.
    """
    global _label_id_cache
    if _label_id_cache:
        return _label_id_cache

    existing = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in existing:
        if label["name"] == _LABEL_NAME:
            _label_id_cache = label["id"]
            return _label_id_cache

    created = service.users().labels().create(
        userId="me",
        body={
            "name": _LABEL_NAME,
            "labelListVisibility": "labelHide",
            "messageListVisibility": "hide",
        },
    ).execute()
    _label_id_cache = created["id"]
    return _label_id_cache


def _list_unprocessed_sync(service) -> list[str]:
    """
    newer_than:7d bounds the search so this doesn't scan the entire
    mailbox history every cycle — approvals are expected to resolve
    within days, not months. Adjust if your review turnaround is slower.
    """
    label_id = _get_or_create_label_id_sync(service)
    query = f'-label:"{_LABEL_NAME}" newer_than:7d'
    result = service.users().messages().list(userId="me", q=query).execute()
    return [m["id"] for m in result.get("messages", [])]


def _fetch_raw_sync(service, message_id: str) -> bytes:
    result = service.users().messages().get(userId="me", id=message_id, format="raw").execute()
    return base64.urlsafe_b64decode(result["raw"])


def _mark_processed_sync(service, message_id: str) -> None:
    label_id = _get_or_create_label_id_sync(service)
    service.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()


async def poll_once(pool) -> list[dict]:
    """
    One poll cycle. Returns resolved approval_log rows the CALLER must
    dispatch a resume for (see hitl.dispatch_resume). Logs a summary line
    every cycle now, even when nothing matched — so "is this actually
    running" is answerable from the log output alone, not a guess.
    """
    service = get_gmail_service()
    resolved_approvals: list[dict] = []

    message_ids = await asyncio.to_thread(_list_unprocessed_sync, service)

    for message_id in message_ids:
        raw_bytes = await asyncio.to_thread(_fetch_raw_sync, service, message_id)
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

        candidates = _extract_candidate_message_ids(msg)
        if not candidates:
            logger.info(
                "Message %r (subject: %r) has no In-Reply-To/References headers — "
                "not a reply to anything, skipping.",
                message_id, msg.get("Subject", ""),
            )
            await asyncio.to_thread(_mark_processed_sync, service, message_id)
            continue  # not a reply to anything we sent — ignore, but still mark processed

        pending = await approval_log.find_pending_by_message_ids(pool, candidates)
        if pending is None:
            logger.warning(
                "Message %r looks like a reply (candidates=%s) but none matched a "
                "PENDING approval_log.outbound_message_id. Either it's a reply to "
                "something already resolved, OR the outbound Message-ID Gmail "
                "actually used differs from what we set/stored — check 'Show "
                "original' on the sent approval email and compare its real "
                "Message-ID header against what's in approval_log.outbound_message_id.",
                message_id, candidates,
            )
            await asyncio.to_thread(_mark_processed_sync, service, message_id)
            continue  # reply to something already resolved, or not ours

        if not _is_from_reviewer(msg):
            logger.warning(
                "Reply to thread_id=%s came from %r, not REVIEWER_EMAIL — ignoring.",
                pending["thread_id"], msg.get("From", ""),
            )
            await asyncio.to_thread(_mark_processed_sync, service, message_id)
            continue

        body = extract_plain_text(msg)
        decision = parse_decision(body)

        if decision is None:
            logger.warning(
                "Could not parse a clear decision for thread_id=%s. First 200 chars: %r",
                pending["thread_id"], body[:200],
            )
            await asyncio.to_thread(_mark_processed_sync, service, message_id)
            continue

        resolved = await approval_log.resolve_pending_approval(pool, pending["thread_id"], decision)
        if resolved is None:
            logger.info("thread_id=%s already resolved, skipping duplicate.", pending["thread_id"])
            await asyncio.to_thread(_mark_processed_sync, service, message_id)
            continue

        logger.info("Resolved thread_id=%s -> %s.", pending["thread_id"], decision)
        await asyncio.to_thread(_mark_processed_sync, service, message_id)
        resolved_approvals.append(resolved)

    logger.info(
        "Poll cycle complete: checked %d candidate message(s), resolved %d.",
        len(message_ids), len(resolved_approvals),
    )
    return resolved_approvals