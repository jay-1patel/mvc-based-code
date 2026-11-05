"""SQLite data layer — public facade.

The concrete queries now live in backend/db/* (core, audit, products, menu,
webhooks, chat, sessions, settings, state, templates, search, admins, gaps,
crawl). This module keeps:
  * the connection/tenant context helpers (via db.core re-exports),
  * the one-time schema init + migration ladder (init_db and friends), and
  * every public name, so `from database import X` is unchanged.
"""
import os  # noqa: F401 - kept for legacy consumers of `database`
import json  # noqa: F401
import sqlite3  # noqa: F401
import logging  # noqa: F401
import hashlib  # noqa: F401
import re  # noqa: F401
import contextvars  # noqa: F401
from ipaddress import ip_address as _parse_ip_address, ip_network  # noqa: F401
from datetime import datetime, timedelta, timezone  # noqa: F401
from contextlib import contextmanager  # noqa: F401
from typing import Optional, Any, Dict, List  # noqa: F401

try:  # backend/ on sys.path (the normal import mode)
    from db.core import (
        DB_PATH, BASE_DIR, PROJECT_ROOT, logger, get_db, get_db_context,
        _ensure_columns, _loads_json, _current_tenant, set_request_tenant,
        get_request_tenant, set_audit_request_context, clear_audit_request_context,
        trusted_client_ip, _resolve_db_path,
    )
    from db.audit import (  # noqa: F401
        _AUDIT_SENSITIVE_KEY_PARTS, _init_admin_audit_tables, _init_audit_logs_table, record_admin_audit_event, record_audit_log_event, safe_audit_details,
    )
    from db.config_drafts import (  # noqa: F401
        get_draft_config, get_publish_history, get_published_config, publish_config, save_draft_config,
    )
    from db.settings import (  # noqa: F401
        get_active_test_tenant, get_app_setting, set_active_test_tenant, set_app_setting,
    )
    from db.products import (  # noqa: F401
        _deserialize_product, _resolve_tenant, _resolve_tenant_ids, _row_to_dict, approve_staged_products, build_products_snapshot, count_products, delete_product, delete_staged_products, get_product, get_product_by_slug, get_products_by_category, get_published_products, list_product_categories, list_products, list_staged_products, product_slug_taken, save_product, search_products, update_product,
    )
    from db.menu import (  # noqa: F401
        delete_menu_item, get_menu_items, get_menu_settings, invalidate_menu_cache, reset_menu, set_menu_settings, upsert_menu_item,
    )
    from db.webhooks import (  # noqa: F401
        WEBHOOK_LOG_MAX_ROWS, _trim_webhook_logs, get_webhook_stats, log_webhook, mark_inbound_processed, was_inbound_processed,
    )
    from db.chat import (  # noqa: F401
        delete_file_chunks, delete_knowledge_base_file, get_files_summary, get_recent_history, save_chat, save_chunks, save_embeddings, save_knowledge_base_chunks,
    )
    from db.sessions import (  # noqa: F401
        get_session_info, is_session_open, update_session_inbound, update_session_outbound,
    )
    from db.state import (  # noqa: F401
        clear_user_context, get_user_state, reopen_conversation_on_inbound, set_human_handover, set_user_state, update_user_context,
    )
    from db.templates import (  # noqa: F401
        deactivate_session_button, delete_template, get_button_click_stats, get_button_clicks, get_session_button, get_template, list_session_buttons, list_templates, log_button_click, save_session_button, save_template, trigger_session_button,
    )
    from db.search import (  # noqa: F401
        search_faq_db, search_knowledge_base_db,
    )
    from db.admins import (  # noqa: F401
        _chat_message_to_dict, _parse_admin_file_row, clear_admin_otp, count_admins, count_admins_by_role, count_tenant_admins, create_admin, delete_admin, delete_admin_chat_message, delete_admin_file, delete_chat_message, get_admin_by_id, get_admin_by_username, get_admin_chat_history, get_admin_chat_preview, get_admin_file, get_admin_otp, get_admin_record, get_all_admins_except, get_chat_history, get_chat_last_message, list_admin_files, list_admin_records, list_admins, mark_chat_message_deleted, save_admin_chat_message, save_admin_file, save_chat_message, set_admin_otp, soft_delete_message_for_everyone, soft_delete_message_for_user, update_admin, update_admin_email, update_admin_file_status, update_admin_record,
    )
    from db.gaps import (  # noqa: F401
        add_faq_from_gap, get_knowledge_gaps_summary, list_unanswered_queries, record_unanswered_query, resolve_unanswered_query,
    )
    from db.crawl import (  # noqa: F401
        _crawl_schedule_row, delete_crawl_schedule, get_crawl_schedule, list_due_crawl_schedules, mark_crawl_finished, mark_crawl_started, upsert_crawl_schedule,
    )
    from db.customers import (  # noqa: F401
        touch_customer, update_customer_profile,
    )
    from db.schema import (  # noqa: F401
        init_db, _has_legacy_unique_on_phone_id, _rebuild_tenants_for_partial_unique, _init_tenancy_tables, _default_tenant_id_for_conn, _init_embedding_tenancy, _has_legacy_global_unique_on_slug, _default_tenant_id, _table_pk_columns, _migrate_composite_tenant_wa_keys, _init_api_onboarding_tables, _init_tenant_change_tables, _ensure_callback_tables, _init_integration_tables, _init_lead_tables, _init_offerings_migration, _migrate_products_per_tenant_slug, _init_record_columns_table, _init_conversation_state_columns,
    )
except ImportError:  # imported as part of the `backend` package
    from backend.db.core import (
        DB_PATH, BASE_DIR, PROJECT_ROOT, logger, get_db, get_db_context,
        _ensure_columns, _loads_json, _current_tenant, set_request_tenant,
        get_request_tenant, set_audit_request_context, clear_audit_request_context,
        trusted_client_ip, _resolve_db_path,
    )
    from backend.db.audit import (  # noqa: F401
        _AUDIT_SENSITIVE_KEY_PARTS, _init_admin_audit_tables, _init_audit_logs_table, record_admin_audit_event, record_audit_log_event, safe_audit_details,
    )
    from backend.db.config_drafts import (  # noqa: F401
        get_draft_config, get_publish_history, get_published_config, publish_config, save_draft_config,
    )
    from backend.db.settings import (  # noqa: F401
        get_active_test_tenant, get_app_setting, set_active_test_tenant, set_app_setting,
    )
    from backend.db.products import (  # noqa: F401
        _deserialize_product, _resolve_tenant, _resolve_tenant_ids, _row_to_dict, approve_staged_products, build_products_snapshot, count_products, delete_product, delete_staged_products, get_product, get_product_by_slug, get_products_by_category, get_published_products, list_product_categories, list_products, list_staged_products, product_slug_taken, save_product, search_products, update_product,
    )
    from backend.db.menu import (  # noqa: F401
        delete_menu_item, get_menu_items, get_menu_settings, invalidate_menu_cache, reset_menu, set_menu_settings, upsert_menu_item,
    )
    from backend.db.webhooks import (  # noqa: F401
        WEBHOOK_LOG_MAX_ROWS, _trim_webhook_logs, get_webhook_stats, log_webhook, mark_inbound_processed, was_inbound_processed,
    )
    from backend.db.chat import (  # noqa: F401
        delete_file_chunks, delete_knowledge_base_file, get_files_summary, get_recent_history, save_chat, save_chunks, save_embeddings, save_knowledge_base_chunks,
    )
    from backend.db.sessions import (  # noqa: F401
        get_session_info, is_session_open, update_session_inbound, update_session_outbound,
    )
    from backend.db.state import (  # noqa: F401
        clear_user_context, get_user_state, reopen_conversation_on_inbound, set_human_handover, set_user_state, update_user_context,
    )
    from backend.db.templates import (  # noqa: F401
        deactivate_session_button, delete_template, get_button_click_stats, get_button_clicks, get_session_button, get_template, list_session_buttons, list_templates, log_button_click, save_session_button, save_template, trigger_session_button,
    )
    from backend.db.search import (  # noqa: F401
        search_faq_db, search_knowledge_base_db,
    )
    from backend.db.admins import (  # noqa: F401
        _chat_message_to_dict, _parse_admin_file_row, clear_admin_otp, count_admins, count_admins_by_role, count_tenant_admins, create_admin, delete_admin, delete_admin_chat_message, delete_admin_file, delete_chat_message, get_admin_by_id, get_admin_by_username, get_admin_chat_history, get_admin_chat_preview, get_admin_file, get_admin_otp, get_admin_record, get_all_admins_except, get_chat_history, get_chat_last_message, list_admin_files, list_admin_records, list_admins, mark_chat_message_deleted, save_admin_chat_message, save_admin_file, save_chat_message, set_admin_otp, soft_delete_message_for_everyone, soft_delete_message_for_user, update_admin, update_admin_email, update_admin_file_status, update_admin_record,
    )
    from backend.db.gaps import (  # noqa: F401
        add_faq_from_gap, get_knowledge_gaps_summary, list_unanswered_queries, record_unanswered_query, resolve_unanswered_query,
    )
    from backend.db.crawl import (  # noqa: F401
        _crawl_schedule_row, delete_crawl_schedule, get_crawl_schedule, list_due_crawl_schedules, mark_crawl_finished, mark_crawl_started, upsert_crawl_schedule,
    )
    from backend.db.customers import (  # noqa: F401
        touch_customer, update_customer_profile,
    )
    from backend.db.schema import (  # noqa: F401
        init_db, _has_legacy_unique_on_phone_id, _rebuild_tenants_for_partial_unique, _init_tenancy_tables, _default_tenant_id_for_conn, _init_embedding_tenancy, _has_legacy_global_unique_on_slug, _default_tenant_id, _table_pk_columns, _migrate_composite_tenant_wa_keys, _init_api_onboarding_tables, _init_tenant_change_tables, _ensure_callback_tables, _init_integration_tables, _init_lead_tables, _init_offerings_migration, _migrate_products_per_tenant_slug, _init_record_columns_table, _init_conversation_state_columns,
    )

