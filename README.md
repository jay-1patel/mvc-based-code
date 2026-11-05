# WhatsApp Business Chatbot

AI-powered WhatsApp chatbot for product inquiries, FAQs, and B2B ordering. Built with **FastAPI + SQLite + Ollama LLM + RAG**.

## Quick Start

```bash
pip install -r requirements.txt
python backend/main.py        # Unified service on port 9000(faq,kb and routing running on port:9000)
```

## Architecture

```
WhatsApp User
    │
    ▼
send2.digital webhook
    │
    ▼
┌──────────────────────────────────────────────────┐
│  routing/webhook.py  (port 9000)                 │
│  Receives WhatsApp messages, classifies query    │
└──────────────┬───────────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
┌─────────────┐  ┌─────────────┐
│  FAQ Bot    │  │  KB Bot     │
│  (Ollama)   │  │  (Ollama)   │
│  Hybrid     │  │  FAISS RAG  │
│  Retrieval  │  │  + Tools    │
└──────┬──────┘  └──────┬──────┘
       │               │
       └───────┬───────┘
               ▼
      send2.digital API
               │
               ▼
         WhatsApp User
```

## Services

| Service | Port | Entry Point | Purpose |
|---------|------|-------------|---------|
| Unified Bot | 9000 | `backend/main.py` | Single service running FAQ + KB + routing |
| KB Bot (standalone) | 9000 | `kb/main.py` | Knowledge base bot with menus and RAG |
| Routing Module | 9000 | `routing/main.py` | Webhook receiver, query classifier, fallback sender |

## Config

Single source of truth: **`routing/config.py`**

All environment variables are loaded from `routing/.env`. The `backend/` and `kb/` directories import from `routing.config` via `sys.path`.

Required env vars:
```
SEND2_USERNAME=your_send2_username
SEND2_PASSWORD=your_send2_password
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
```

## Directory Structure

```
web/
├── routing/
│   ├── config.py              # Single config (all env vars)
│   ├── message.py             # Query classification + bot forwarding
│   ├── classifier.py          # FAQ/KB/greeting classifier
│   ├── webhook.py             # WhatsApp webhook receiver
│   └── whatsapp.py            # Text/media send fallback
├── backend/
│   ├── main.py                # Unified FastAPI app
│   ├── database.py            # SQLite schema + CRUD
│   ├── uploaded_files/        # Media storage (faq/, kb/, products/)
│   ├── faq/
│   │   ├── service.py         # Hybrid retrieval + Ollama generation
│   │   └── router.py          # FAQ API endpoints
│   ├── routes/                # Admin, auth, chat, catalog routes
│   └── extract.py             # File text extraction
├── kb/
│   ├── main.py                # Standalone KB bot entry point
│   ├── router.py              # KB API endpoints
│   ├── services/
│   │   ├── brain.py           # LLM orchestration (Ollama/Groq/Mistral)
│   │   ├── kb_handler.py      # Central message handler + dynamic buttons
│   │   ├── menu_router.py     # Conversation FSM (state machine)
│   │   ├── rag.py             # FAISS-based RAG
│   │   ├── tools.py           # 10 registered LLM tools
│   │   ├── whatsapp.py        # WhatsApp API (buttons, lists, media)
│   │   └── ...
│   └── dataset/               # Seed data (KB docs, FAQ JSON)
└── dataset/                   # FAQ + knowledge base seed data
```

## Interactive Messages

See [INTERACTIVE_MESSAGES.md](INTERACTIVE_MESSAGES.md) for full details on how buttons, lists, and dynamic buttons work.

## Ollama & Speed

See [OLLAMA_SPEED.md](OLLAMA_SPEED.md) for details on how both FAQ and KB bots use Ollama for fast answer generation.

## Data Coverage

| Topic | KB Docs | FAQ Entries |
|-------|---------|-------------|
| Product Catalogue | Full catalog with SKUs, prices, pack sizes | 3 FAQs |
| Product Specifications | Per-variant specs, weight, dimensions, ingredients | Covered in catalog FAQ |
| Nutrition Information | Per-100g nutrition table for all variants | 2 FAQs |
| FAQs | Pre-seeded knowledge | 50 FAQs |
| Company Policies | Privacy, Terms, Cancellation, Quality, Grievance | 1 FAQ |
| Return Policy | Full return/replacement policy with timelines | 3 FAQs |
| Shipping Policy | Zones, charges, tracking, delivery times, partners | 5 FAQs |
| Brand Story | Founder journey, mission, awards | 1 FAQ |
| Certifications | FSSAI, GMP, ISO, HACCP, Organic, Veg, Gluten-free | 2 FAQs |

**Total:** 19 KB documents + 50 FAQs

To add new data: edit `dataset/*.json`, then run `python seed_new.py`.

## LLM Tool Calling (KB Bot)

The KB bot has 10 registered tools that the LLM can call:

| Tool | Description |
|------|-------------|
| `search_products` | Search products by name/keyword |
| `get_product_details` | Get detailed product info |
| `get_nutrition_info` | Nutrition facts with portion calculation |
| `check_order_status` | Order status lookup |
| `get_faq_answer` | FAQ database lookup |
| `get_contact_details` | Business contact info |
| `human_handover` | Escalate to human agent |
| `list_categories` | List product categories |
| `get_products_by_category` | Products in a category |
| `calculate_margin` | B2B margin calculator |

## WhatsApp Integration

Uses **send2.digital** CPaaS API for all messaging:
- **Text messages** — plain text responses
- **Interactive buttons** — max 3 reply buttons per message
- **List menus** — for menus with 3+ options (main menu, product catalog)
- **Media** — images, documents, videos via upload or URL


Does NOT delete existing data. Safe to run multiple times.
