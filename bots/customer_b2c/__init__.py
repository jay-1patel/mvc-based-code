"""
customer_b2c
============

B2C Customer Support persona.

Handles the retail customer experience: browsing products, nutrition info,
recipe suggestions, order tracking, complaint registration, shipping help,
and human handover.

Submodules:
    config      - B2C-specific states, prompts and thresholds.
    menus       - JSON definitions for B2C WhatsApp list menus.
    tools       - B2C business logic (nutrition, recipes, complaints).
    workflows   - B2C state-machine handler routed by the orchestrator.
"""
