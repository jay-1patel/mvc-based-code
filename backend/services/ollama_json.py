import json, logging, re, threading, time
import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3:8b"
_TIMEOUT = 120
_SEM = threading.Semaphore(2)

def _extract_json(text):
    text = text.strip()
    # remove think blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    # find first json object/array
    for opener, closer in (("{", "}"), ("[", "]")):
        i = text.find(opener)
        if i != -1:
            j = text.rfind(closer)
            if j > i:
                cand = text[i:j+1]
                try:
                    return json.loads(cand)
                except Exception:
                    # deliberate: candidate extraction — the next candidate shape
                    # is tried.
                    logger.debug("JSON_CANDIDATE_PARSE_SKIPPED | opener=%s", opener, exc_info=True)
    try:
        return json.loads(text)
    except Exception:
        return None

def call_json(prompt, system=None, retries=1):
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    if system:
        payload["system"] = system
    last_err = None
    for attempt in range(retries + 1):
        try:
            with _SEM:
                r = requests.post(OLLAMA_URL, json=payload, timeout=_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            obj = _extract_json(data.get("response", ""))
            if obj is not None:
                return obj
            last_err = ValueError("invalid json")
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise last_err if last_err else ValueError("ollama failed")
PY