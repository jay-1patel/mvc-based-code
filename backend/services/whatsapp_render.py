"""
WhatsApp delivery renderer for the test simulator (parity core).

The orchestrator (``services.orchestrator.process_incoming_message``) returns
*captured* payloads — the bot's internal message shapes::

    {"type": "text", "text": ...}
    {"type": "buttons", "text": ..., "buttons": [...], "header_media": {...}}
    {"type": "list", "header": ..., "body": ..., "button_text": ..., "sections": [...]}
    {"type": "cta_url", "text": ..., "cta_url": ..., "display_text": ..., ...}
    {"type": "image", "media_url": ..., "caption": ...}
    {"type": "document", "media_url": ..., "filename": ...}

Those are NOT byte-for-byte what lands on a customer's phone. The production
delivery layer (``services.incoming_listener._send_reply`` on the send2
poller path, dispatching into ``routing.whatsapp`` / routing_sender) first:

  * normalizes Markdown-ish text into WhatsApp formatting
    (``normalize_whatsapp_formatting``) and chunks text at 4000 chars,
  * shapes interactive payloads to WhatsApp's limits (button titles <= 20,
    max 3 reply buttons; list row titles <= 24, descriptions <= 72,
    section titles <= 24, header <= 60, button text <= 20, duplicate row
    ids suffixed),
  * splits an over-long interactive body (> 950 chars) into a preceding
    plain-text message plus a "Please select an option below:" prompt,
  * sends a buttons message's ``header_media`` as a separate media message
    first, an image's caption as a separate text message after it, and
    falls back to plain text when a buttons/CTA payload has nothing
    actionable left.

This module applies that exact shaping — reusing the production helpers
(``normalize_whatsapp_formatting``, ``normalize_reply_buttons``,
``_chunk_text``, ``_truncate_body``) rather than re-implementing them — and
returns *phone messages*: the sequence a WhatsApp user actually receives.
The WhatsApp Tester renders these, so what the admin sees in the panel is
what the customer gets on the phone.

Phone message shapes::

    {"kind": "text", "text": str, "source_index": int}
    {"kind": "image", "media_url": str, "source_index": int}
    {"kind": "document", "media_url": str, "filename": str, "source_index": int}
    {"kind": "buttons", "text": str,
     "buttons": [{"id": str, "title": str}], "source_index": int}
    {"kind": "list", "header": str, "body": str, "footer": str,
     "button_text": str,
     "sections": [{"title": str, "rows": [{"id", "title", "description"}]}],
     "source_index": int}
    {"kind": "cta_url", "text": str, "display_text": str, "url": str,
     "image_url": str, "footer": str, "source_index": int}

``source_index`` is the index of the captured payload the phone message was
rendered from (a split body yields two phone messages with the same index),
so the panel can line the rendered view up with the raw payload view.

One deliberate divergence from the live path: media URLs are resolved
conservatively (public http(s) as-is, otherwise prefixed with the configured
public base URL). The live ``_resolve_send_url`` may additionally re-host a
local file on an external image host; a dry-run capture must never trigger
an upload, so the renderer does not call it.
"""

import logging

logger = logging.getLogger("whatsapp_render")

# Body length above which the production senders split an interactive
# message: full text goes out as plain text first, the interactive body
# becomes a short prompt. Mirrors routing_sender's send paths.
_INTERACTIVE_SPLIT_THRESHOLD = 950
_INTERACTIVE_BODY_LIMIT = 1020
_TEXT_CHUNK_LIMIT = 4000


def _sender():
    """The production routing sender module (routing.whatsapp alias)."""
    try:
        from routing import whatsapp as sender
    except ImportError:  # pragma: no cover - package-style import
        from backend.app.services.whatsapp import routing_sender as sender
    return sender


def _resolve_media_url(media_url: str) -> str:
    """Make a media URL public the conservative, side-effect-free way."""
    if not media_url:
        return ""
    if media_url.startswith(("http://", "https://")):
        return media_url
    try:
        from routing.config import PUBLIC_BASE_URL
    except ImportError:  # pragma: no cover
        return media_url
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        return base + "/" + media_url.lstrip("/")
    return media_url


def _text_messages(sender, text: str, source_index: int) -> list:
    """Phone messages for a production plain-text send of ``text``."""
    formatted = sender.normalize_whatsapp_formatting(text or "")
    chunks = sender._chunk_text(formatted, limit=_TEXT_CHUNK_LIMIT)
    if not chunks:
        chunks = [" "]
    return [
        {"kind": "text", "text": chunk, "source_index": source_index}
        for chunk in chunks
    ]


def _header_media_link(header_media) -> tuple:
    """(url, msg_type) from a captured header_media payload.

    Mirrors ``services.incoming_listener._header_media_link``.
    """
    if not isinstance(header_media, dict):
        return None, "document"
    htype = (header_media.get("type") or "document").lower()
    block = (
        header_media.get(htype)
        or header_media.get("document")
        or header_media.get("image")
        or {}
    )
    if isinstance(block, dict):
        url = block.get("link") or block.get("url") or ""
    else:
        url = ""
    if not url:
        return None, htype if htype in ("image", "document", "video", "audio") else "document"
    return url, htype if htype in ("image", "document", "video", "audio") else "document"


def _media_message(kind: str, url: str, source_index: int, filename: str = "") -> dict:
    if kind == "image":
        return {"kind": "image", "media_url": url, "source_index": source_index}
    return {
        "kind": "document",
        "media_url": url,
        "filename": filename or "",
        "source_index": source_index,
    }


def _render_text(sender, msg: dict, idx: int) -> list:
    text = msg.get("text") or ""
    if not text:
        return []
    return _text_messages(sender, text, idx)


def _render_image(sender, msg: dict, idx: int) -> list:
    out = []
    url = _resolve_media_url(msg.get("media_url") or "")
    if url:
        out.append(_media_message("image", url, idx))
    caption = (msg.get("caption") or "").strip()
    if caption:
        out.extend(_text_messages(sender, caption, idx))
    return out


def _render_document(sender, msg: dict, idx: int) -> list:
    url = _resolve_media_url(msg.get("media_url") or "")
    if not url:
        return []
    return [_media_message("document", url, idx, msg.get("filename") or "")]


def _render_buttons(sender, msg: dict, idx: int) -> list:
    out = []

    # header_media is delivered as its own media message first.
    hm_url, hm_type = _header_media_link(msg.get("header_media"))
    if hm_url:
        resolved = _resolve_media_url(hm_url)
        if resolved:
            out.append(_media_message(hm_type, resolved, idx))

    text = msg.get("text") or ""
    api_buttons = sender.normalize_reply_buttons(msg.get("buttons") or [])
    if text and api_buttons:
        formatted = sender.normalize_whatsapp_formatting(text)
        if len(formatted) > _INTERACTIVE_SPLIT_THRESHOLD:
            out.extend(_text_messages(sender, text, idx))
            body = "Please select an option below:"
        else:
            body = sender._truncate_body(formatted, limit=_INTERACTIVE_BODY_LIMIT)
        buttons = [
            {
                "id": b.get("reply", {}).get("id", ""),
                "title": str(b.get("reply", {}).get("title", ""))[:20],
            }
            for b in api_buttons[:3]
        ]
        out.append(
            {"kind": "buttons", "text": body, "buttons": buttons, "source_index": idx}
        )
    elif text:
        out.extend(_text_messages(sender, text, idx))
    return out


def _shape_list_sections(sections: list) -> list:
    """Section/row shaping exactly as send_whatsapp_list_menu applies it."""
    api_sections = []
    seen_row_ids: set = set()
    for section in sections or []:
        api_section = {"title": str(section.get("title", "") or "")[:24], "rows": []}
        for row in section.get("rows", []):
            rid = str(row.get("id", "") or "")[:200]
            if not rid:
                continue
            base = rid
            n = 2
            while rid in seen_row_ids:
                suffix = f"_{n}"
                rid = base[: 200 - len(suffix)] + suffix
                n += 1
            seen_row_ids.add(rid)
            api_section["rows"].append(
                {
                    "id": rid,
                    "title": str(row.get("title", "") or "")[:24],
                    "description": str(row.get("description", "") or "")[:72],
                }
            )
        api_sections.append(api_section)
    return api_sections


def _render_list(sender, msg: dict, idx: int) -> list:
    header = msg.get("header", "")
    body = msg.get("body", "")
    button_text = msg.get("button_text", "Menu")
    sections = msg.get("sections", [])
    # Production sends nothing for a list with an empty body or no sections.
    if not (body and sections):
        return []

    out = []
    raw_body = sender.normalize_whatsapp_formatting(body or " ")
    if len(raw_body) > _INTERACTIVE_SPLIT_THRESHOLD:
        out.extend(_text_messages(sender, body, idx))
        norm_body = "Please select an option below:"
    else:
        norm_body = sender._truncate_body(raw_body, limit=_INTERACTIVE_BODY_LIMIT)

    out.append(
        {
            "kind": "list",
            "header": str(header or " ")[:60],
            "body": norm_body or " ",
            "footer": str(msg.get("footer") or "")[:60],
            "button_text": str(button_text or "Menu")[:20],
            "sections": _shape_list_sections(sections),
            "source_index": idx,
        }
    )
    return out


def _render_cta_url(sender, msg: dict, idx: int) -> list:
    image_url = _resolve_media_url(msg.get("image_url") or "")
    cta_url = (msg.get("cta_url") or "").strip()
    body = msg.get("text") or ""

    if cta_url:
        label = str(msg.get("display_text") or "Learn More").strip()[:20] or "Learn More"
        return [
            {
                "kind": "cta_url",
                "text": sender._truncate_body(
                    sender.normalize_whatsapp_formatting(body or " ") or " ",
                    limit=_INTERACTIVE_BODY_LIMIT,
                ),
                "display_text": label,
                "url": cta_url,
                "image_url": image_url,
                "footer": str(msg.get("footer_text") or "")[:60],
                "source_index": idx,
            }
        ]
    out = []
    if image_url:
        out.append(_media_message("image", image_url, idx))
    if body:
        out.extend(_text_messages(sender, body, idx))
    return out


_RENDERERS = {
    "text": _render_text,
    "image": _render_image,
    "document": _render_document,
    "buttons": _render_buttons,
    "list": _render_list,
    "cta_url": _render_cta_url,
}


def render_phone_messages(messages: list) -> list:
    """Render captured orchestrator payloads into phone-visible messages.

    Applies the production delivery layer's shaping (see module docstring).
    Unknown payload types are skipped, exactly as the live dispatcher does.
    """
    sender = _sender()
    out: list = []
    for idx, msg in enumerate(messages or []):
        if not isinstance(msg, dict):
            continue
        renderer = _RENDERERS.get(msg.get("type", "text"))
        if renderer is None:
            logger.warning("RENDER_SKIP | unknown captured type=%r", msg.get("type"))
            continue
        out.extend(renderer(sender, msg, idx))
    return out
