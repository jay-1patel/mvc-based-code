# WhatsApp Business API: Core Concepts

Endpoints cover message sending, template creation/management, credit balance checking, delivery reporting, and session messaging.

Security: API credentials assigned by provider MUST be stored securely on server; NEVER expose credentials in chatbot replies, code, or logs.

Template Messages: Requires approved template name, recipient number with country code, media type/links, and variables. Categories: MARKETING, UTILITY, AUTHENTICATION.

Session Messages: Supports direct message sending (text, image, audio, video, document, location, sticker, contacts) within active 24-hr customer window.