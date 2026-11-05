import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from routing.config import logger
from shared.tenancy.paths import BACKEND_CLIENTS_DIR

CLIENTS_DIR = BACKEND_CLIENTS_DIR
CLIENTS_DIR.mkdir(exist_ok=True)

_client_cache: Dict[str, Dict] = {}


def _client_dir(client_id: str) -> Path:
    d = CLIENTS_DIR / client_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "knowledge").mkdir(exist_ok=True)
    return d


def get_client_config(client_id: str) -> Dict:
    if client_id in _client_cache:
        return _client_cache[client_id]

    config_path = _client_dir(client_id) / "config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            _client_cache[client_id] = cfg
            return cfg
        except Exception as e:
            logger.error(f"Failed to load client config for {client_id}: {e}")

    from .bot_config import load_bot_config
    default = load_bot_config().copy()
    default["client_id"] = client_id
    return default


def save_client_config(client_id: str, config: Dict) -> bool:
    try:
        config_path = _client_dir(client_id) / "config.json"
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        _client_cache[client_id] = config
        logger.info(f"Client config saved: {client_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to save client config for {client_id}: {e}")
        return False


def create_client(client_id: str, name: str, personality: str = "", **kwargs) -> Dict:
    config = {
        "client_id": client_id,
        "name": name,
        "personality": personality or f"You are a helpful WhatsApp assistant for {name}.",
        "guardrails": kwargs.get("guardrails", "Never invent information. Escalate if unsure."),
        "tools": kwargs.get("tools", ["search_products", "get_faq_answer", "human_handover"]),
        "business_knowledge": "./knowledge/",
        "languages": kwargs.get("languages", ["en"]),
        "response_settings": kwargs.get("response_settings", {
            "max_tokens": 1024,
            "temperature": 0.7,
            "similarity_threshold": 0.40,
            "context_window": 3,
        }),
        "escalation_rules": kwargs.get("escalation_rules", {
            "trigger_phrases": ["angry", "complaint", "human", "person"],
            "max_failed_attempts": 2,
            "escalation_message": "I'll connect you with our team.",
        }),
        "welcome_message": kwargs.get("welcome_message", f"Welcome to {name}! How can I help?"),
        "fallback_message": kwargs.get("fallback_message", "I don't have that information. Please ask something else."),
    }
    save_client_config(client_id, config)
    logger.info(f"Client created: {client_id} ({name})")
    return config


def list_clients() -> List[Dict]:
    clients = []
    if not CLIENTS_DIR.exists():
        return clients
    for d in CLIENTS_DIR.iterdir():
        if d.is_dir():
            config_path = d / "config.json"
            if config_path.exists():
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    clients.append({
                        "client_id": cfg.get("client_id", d.name),
                        "name": cfg.get("name", d.name),
                        "personality": cfg.get("personality", "")[:100],
                    })
                except Exception:
                    clients.append({"client_id": d.name, "name": d.name})
    return clients


def delete_client(client_id: str) -> bool:
    import shutil
    client_path = _client_dir(client_id)
    try:
        shutil.rmtree(client_path)
        _client_cache.pop(client_id, None)
        logger.info(f"Client deleted: {client_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete client {client_id}: {e}")
        return False


def get_client_stats(client_id: str) -> Dict:
    config = get_client_config(client_id)
    client_path = _client_dir(client_id)
    knowledge_dir = client_path / "knowledge"
    knowledge_files = list(knowledge_dir.glob("*")) if knowledge_dir.exists() else []
    return {
        "client_id": client_id,
        "name": config.get("name", ""),
        "tools_enabled": config.get("tools", []),
        "knowledge_files": len(knowledge_files),
        "has_config": (client_path / "config.json").exists(),
    }
