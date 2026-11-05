"""Compatibility alias: this module moved into the WhatsApp services home.

The implementation now lives in the WhatsApp services home under the
application package (backend.app.services.whatsapp.flows.distributor_b2b.ticket_flow). This file aliases that module object
under the old import path so existing imports — including code that
patches attributes on this module — behave exactly as before.
"""

import sys as _sys

from backend.app.services.whatsapp.flows.distributor_b2b import ticket_flow as _impl

_sys.modules[__name__] = _impl
