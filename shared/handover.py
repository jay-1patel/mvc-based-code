"""Human-handover copy and resume helpers shared by webhook + orchestrator.

While ``human_handover`` is active the bot stays silent so an agent can reply.
Users can exit that wait anytime by typing a resume phrase (e.g. ``hi``).
"""

from __future__ import annotations

HANDOVER_RESUME_HINT = "Type *hi* anytime to continue with the chatbot."

HANDOVER_CONFIRMATION = (
    "You're now connected to a human agent. Please hold on while we pick up "
    f"your conversation. 🙏\n\n{HANDOVER_RESUME_HINT}"
)

HANDOVER_BOT_RESUME = "You're back with the bot. How can I help you? 🙏"

# Exact-match resume tokens (short words must not match as substrings —
# e.g. "this" must not resume because it contains "hi").
RESUME_EXACT = {
    "bot",
    "resume",
    "hi",
    "hello",
    "hey",
    "menu",
    "start",
    "hii",
    "hiii",
    "helo",
    "heyy",
}

# Multi-word phrases may appear anywhere in the cleaned text.
RESUME_PHRASES = {
    "resume bot",
    "back to bot",
    "bot mode",
    "continue with bot",
    "main menu",
}


def wants_bot_resume(text: str) -> bool:
    """Return True when the user is asking to leave human-handover for the bot."""
    clean = (text or "").strip().lower()
    if not clean:
        return False
    if clean in RESUME_EXACT or clean in RESUME_PHRASES:
        return True
    return any(phrase in clean for phrase in RESUME_PHRASES)


def append_resume_hint(text: str) -> str:
    """Append the hi-to-continue hint unless the message already has it."""
    body = (text or "").rstrip()
    if not body:
        return HANDOVER_RESUME_HINT
    if "type *hi*" in body.lower() or "type hi" in body.lower():
        return body
    return f"{body}\n\n{HANDOVER_RESUME_HINT}"
