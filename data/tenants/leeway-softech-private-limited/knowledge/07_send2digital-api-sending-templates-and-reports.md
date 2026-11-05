# Send2Digital API: Sending, Templates and Reports

Sending: Requires provider username/password, approved template name, recipient number with country code, and parameter variables.

Templates: Configured with name/ID, category, language, body, optional footer/buttons. Must be approved prior to live use.

Reports: API supports date-range queries, message ID lookups, and incoming message logs. Interpret returned fields strictly without inventing delivery status.

Best Practices: HTTPS only, redact secrets, validate recipient formats, handle timeouts gracefully to prevent duplicate sends.