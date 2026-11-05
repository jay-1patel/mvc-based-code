"""
PII masking filter for all log output.
"""
import re
import logging


class PIIMaskingFilter(logging.Filter):
    """Masks Indian mobiles + emails in ALL log output."""
    _PATTERNS = [
        (re.compile(r"\+91[6-9]\d{9}"), "+91XXXXXXXXXX"),
        (re.compile(r"\b[6-9]\d{9}\b"), "XXXXXXXXXX"),
        (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "***@***.***"),
    ]

    def filter(self, record):
        if isinstance(record.msg, str):
            for pat, rep in self._PATTERNS:
                record.msg = pat.sub(rep, record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._mask(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._mask(a) for a in record.args)
        return True

    def _mask(self, v):
        if not isinstance(v, str):
            return v
        for pat, rep in self._PATTERNS:
            v = pat.sub(rep, v)
        return v


def configure_logging() -> None:
    """Call once at app startup."""
    f = PIIMaskingFilter()
    logging.root.addFilter(f)
    for h in logging.root.handlers:
        h.addFilter(f)
