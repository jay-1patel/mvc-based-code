"""Local LLM helper backed by Ollama (OpenAI-compatible endpoint).

Replaces all external Groq / Mistral / OpenRouter API calls with the
local Ollama server. Uses the OpenAI-compatible /v1/chat/completions
route so response parsing matches the previous providers.
"""
import logging
import subprocess
import os

import requests

from routing.config import (
    OLLAMA_API_URL,
    OLLAMA_MODEL,
    OLLAMA_NUM_GPU,
    OLLAMA_NUM_CTX,
    OLLAMA_NUM_BATCH,
    OLLAMA_NUM_THREAD,
    OLLAMA_KEEP_ALIVE,
)

logger = logging.getLogger("llm")


def _log_gpu_stats():
    """Log GPU statistics. SECURITY FIX: Explicit shell=False and absolute paths."""
    try:
        # SECURITY FIX: Use absolute paths, shell=False explicitly, no user input
        nvidia_smi_path = "/usr/bin/nvidia-smi" if os.name != "nt" else "nvidia-smi"
        command = [
            nvidia_smi_path,
            "--query-gpu=utilization.gpu,memory.used,memory.total,name",
            "--format=csv,noheader,nounits"
        ]
        
        if os.name == "nt":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            result = subprocess.run(
                command,
                capture_output=True, 
                text=True, 
                timeout=5, 
                startupinfo=si,
                shell=False  # SECURITY FIX: Explicitly disable shell
            )
        else:
            result = subprocess.run(
                command,
                capture_output=True, 
                text=True, 
                timeout=5,
                shell=False  # SECURITY FIX: Explicitly disable shell
            )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 4:
                    util, mem_used, mem_total, gpu_name = parts
                    logger.info(
                        f"GPU_STATS | name={gpu_name} | util={util}% | mem={mem_used}/{mem_total}MB"
                    )
                    return
        logger.warning("GPU_STATS | nvidia-smi returned no GPU data")
    except FileNotFoundError:
        logger.warning("GPU_STATS | nvidia-smi not found — GPU logging unavailable")
    except Exception as e:
        logger.warning(f"GPU_STATS | failed: {e}")


def _post(messages, max_tokens, temperature, tools=None, tool_choice="auto", timeout=120):
    _log_gpu_stats()
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "think": False,
        "options": {
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": max_tokens,
            "num_gpu": OLLAMA_NUM_GPU,
            "num_thread": OLLAMA_NUM_THREAD,
            "num_batch": OLLAMA_NUM_BATCH,
            "flash_attention": True,
            "temperature": temperature,
            "top_p": 0.9,
            "repeat_penalty": 1.0,
            "seed": 42,
        },
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    logger.info(
        f"LLM_CALL | model={OLLAMA_MODEL} | max_tokens={max_tokens} | temp={temperature} | "
        f"gpu={OLLAMA_NUM_GPU} | ctx={OLLAMA_NUM_CTX} | batch={OLLAMA_NUM_BATCH} | thread={OLLAMA_NUM_THREAD}"
    )
    return requests.post(OLLAMA_API_URL, json=payload, headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "true"}, timeout=timeout)


def chat(messages, max_tokens=1024, temperature=0.7, timeout=120) -> str:
    """Send a chat request and return the assistant's text content (stripped)."""
    resp = _post(messages, max_tokens, temperature, timeout=timeout)
    resp.raise_for_status()
    return (resp.json()["message"].get("content") or "").strip()
