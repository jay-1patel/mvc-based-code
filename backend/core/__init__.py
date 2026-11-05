"""Shared backend core utilities (logging setup, request context).

This package is imported in two styles depending on the entry point —
``core.…`` (apps run from inside the backend folder) and ``backend.core.…``
(apps run from the project root). Both styles load the same files; each
process should stick to ONE style so module identity (and the request-id
context variable) stays single.
"""
