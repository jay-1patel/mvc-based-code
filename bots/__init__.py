"""
bots
====

Top-level package for the dual-persona chatbot system.

Two strictly separated personas live under this package:

- ``bots.distributor_b2b``: B2B Distributor assistant (orders, finance, support).
- ``bots.customer_b2c``: B2C Customer Support assistant (browse, nutrition,
  orders, complaints, human handover).

Both modules only ever interact with the rest of the application through the
main orchestrator (``services.orchestrator``). They reuse the existing shared
infrastructure (WhatsApp sender, database context, RAG pipeline) and must NOT
duplicate any connection pools, API clients, or model loaders.

To avoid circular-import crashes at FastAPI startup, every cross-module import
inside these packages is lazy (performed inside functions).
"""
