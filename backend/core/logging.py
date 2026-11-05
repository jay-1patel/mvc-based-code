"""The ONE logging setup for the whole backend (Phase 2).

Before Phase 2, six different files called ``logging.basicConfig`` with
slightly different settings, and library modules configured logging on
import. Now:

- Entry points call :func:`setup_logging` exactly once, as early as possible.
- Library modules never configure logging — they just
  ``logger = logging.getLogger(__name__)`` and emit.
- Every line uses ONE format:
  ``%(asctime)s [%(levelname)s] %(name)s: %(message)s``
  plus a `` | req=<request-id>`` suffix on lines emitted while handling a
  request (read from the shared context variable in ``core.context``), so a
  log line can be tied back to the browser-side error id.

Idempotent: calling it again (or from a second entry point in the same
process) is a no-op.
"""
import logging

from .context import request_id_var

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s%(req_suffix)s"
_MARKER = "_single_logging_setup_done"


class _RequestContextFilter(logging.Filter):
    """Attach the request-context suffix to every record.

    Always sets ``record.req_suffix`` (empty string outside a request) so the
    shared formatter can reference it unconditionally.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rid = request_id_var.get()
        except Exception:
            rid = ""
        record.req_suffix = f" | req={rid}" if rid else ""
        return True


def _pii_filter():
    """Best-effort: the project's PII masking filter, if importable.

    The filter lives with the backend services; depending on the entry point
    it is reachable under either import style. If neither works, logging
    still works — masking is a privacy extra, never a startup dependency.
    """
    import importlib

    for mod_name in ("services.logging_config", "backend.services.logging_config"):
        try:
            mod = importlib.import_module(mod_name)
            return mod.PIIMaskingFilter()
        except Exception:
            logging.getLogger(__name__).debug(
                "PII_FILTER_IMPORT_SKIPPED | module=%s", mod_name, exc_info=True
            )
    return None


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once. Safe to call from any entry point."""
    root = logging.getLogger()
    if getattr(root, _MARKER, False):
        return
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(_RequestContextFilter())
        pii = _pii_filter()
        if pii is not None:
            handler.addFilter(pii)
        root.addHandler(handler)
    else:
        # Something already configured handlers (e.g. a test runner or a
        # hosting wrapper): keep them, but still attach the request-context
        # filter so records carry the suffix attribute for any formatter
        # that uses it.
        ctx_filter = _RequestContextFilter()
        for handler in root.handlers:
            handler.addFilter(ctx_filter)
    root.setLevel(level)
    setattr(root, _MARKER, True)
