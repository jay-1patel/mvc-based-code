"""
distributor_b2b
===============

B2B Distributor persona.

Handles the professional distributor experience: placing orders, tracking
orders, checking outstanding payments, paying invoices, and getting support
(catalog / assigned sales rep).

Submodules:
    config      - B2B-specific states, prompts and thresholds.
    menus       - JSON definitions for B2B WhatsApp list menus.
    tools       - B2B business logic (orders, finance, sales rep).
    workflows   - B2B state-machine handler routed by the orchestrator.
"""
