"""
Unified API Routes - Multi-bot support with shared context

This module provides unified endpoints for all bot types:
- /chat - Process messages through intelligent routing
- /context - Get comprehensive customer context
- /analytics - Cross-bot analytics and reporting
- /bots - Bot management and capabilities
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import logging

# Import the unified service
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.unified_bot import unified_service, BotType

logger = logging.getLogger("unified_api")

router = APIRouter(prefix="/api/unified", tags=["unified"])

# Request/Response Models
class ChatMessage(BaseModel):
    """Request model for chat messages"""
    wa_id: str = Field(..., description="WhatsApp ID of the user")
    message: str = Field(..., description="Message text from user")
    sender_name: Optional[str] = Field(None, description="Name of the sender")
    bot_type: Optional[BotType] = Field(None, description="Force routing to specific bot")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")

class ChatResponse(BaseModel):
    """Response model for chat messages"""
    success: bool
    bot_type: BotType
    response: str
    customer_context: Dict[str, Any]
    routing_metadata: Dict[str, Any]
    error: Optional[str] = None

class CustomerContext(BaseModel):
    """Customer context response"""
    wa_id: str
    profile: Dict[str, Any]
    recent_interactions: Dict[str, Any]
    support_context: Dict[str, Any]
    sales_context: Dict[str, Any]
    product_interests: Dict[str, Any]
    routing_metadata: Dict[str, Any]

class BotCapability(BaseModel):
    """Bot capability information"""
    bot_type: BotType
    name: str
    description: str
    capabilities: List[str]
    priority: int
    status: str

class AnalyticsResponse(BaseModel):
    """Analytics response"""
    time_range_days: int
    total_messages: int
    active_users: int
    bot_distribution: Dict[str, int]
    most_used_bot: Optional[str]
    generated_at: str

class BotHealth(BaseModel):
    """Health status of bots"""
    bot_type: BotType
    status: str
    uptime_percentage: float
    avg_response_time_ms: float
    error_rate: float

# ── Main Chat Endpoint ───────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse)
async def process_chat_message(request: ChatMessage) -> ChatResponse:
    """
    Main chat endpoint - Process messages through intelligent routing

    This endpoint automatically routes messages to the appropriate bot based on:
    - Active support tickets
    - Sales intent detection
    - Product inquiries
    - FAQ matches
    - General conversation patterns

    You can override automatic routing by specifying `bot_type`.
    """
    try:
        logger.info(f"Processing chat message for {request.wa_id}")

        # Process through unified service
        result = unified_service.process_message(
            wa_id=request.wa_id,
            message=request.message,
            sender_name=request.sender_name,
            bot_type=request.bot_type,
            metadata=request.metadata
        )

        return ChatResponse(**result)

    except Exception as e:
        logger.error(f"Error processing chat message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Customer Context Endpoint ────────────────────────────────────────────────

@router.get("/context/{wa_id}", response_model=CustomerContext)
async def get_customer_context(
    wa_id: str,
    include_history: bool = Query(True, description="Include detailed chat history"),
    history_limit: int = Query(20, description="Number of recent messages to include")
) -> CustomerContext:
    """
    Get comprehensive customer context across all bots

    Returns:
    - User profile and preferences
    - Recent chat history across all bots
    - Active support tickets
    - Sales lead information
    - Product interests and interactions
    """
    try:
        logger.info(f"Getting customer context for {wa_id}")

        # Get context from unified service
        context = unified_service.get_customer_context(wa_id)

        # Optionally limit history
        if not include_history:
            context['recent_interactions']['chat_history'] = []

        if history_limit < len(context.get('recent_interactions', {}).get('chat_history', [])):
            context['recent_interactions']['chat_history'] = \
                context['recent_interactions']['chat_history'][:history_limit]

        return CustomerContext(**context)

    except Exception as e:
        logger.error(f"Error getting customer context: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Analytics Endpoints ───────────────────────────────────────────────────────

@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    time_range: int = Query(30, description="Time range in days", ge=1, le=365),
    wa_id: Optional[str] = Query(None, description="Filter by specific user")
) -> AnalyticsResponse:
    """
    Get cross-bot analytics and metrics

    Parameters:
    - time_range: Number of days to look back (1-365)
    - wa_id: Optional user ID to filter analytics

    Returns:
    - Total message count
    - Active user count
    - Bot usage distribution
    - Most used bot
    """
    try:
        logger.info(f"Getting analytics for range {time_range} days")

        analytics = unified_service.get_cross_bot_analytics(
            wa_id=wa_id,
            time_range=time_range
        )

        return AnalyticsResponse(**analytics)

    except Exception as e:
        logger.error(f"Error getting analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/analytics/per-bot")
async def get_per_bot_analytics(
    time_range: int = Query(30, description="Time range in days", ge=1, le=365)
) -> Dict[str, Any]:
    """Get detailed analytics for each bot type"""
    try:
        analytics = unified_service.get_cross_bot_analytics(time_range=time_range)

        # Enhance with per-bot details
        bot_details = {}
        for bot_type, count in analytics.get('bot_distribution', {}).items():
            bot_info = unified_service.bot_capabilities.get(bot_type, {})
            bot_details[bot_type] = {
                'name': bot_info.get('name', bot_type),
                'message_count': count,
                'percentage': round((count / analytics.get('total_messages', 1)) * 100, 2),
                'capabilities': bot_info.get('capabilities', []),
                'status': bot_info.get('status', 'unknown')
            }

        return {
            'summary': analytics,
            'bot_details': bot_details,
            'generated_at': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error getting per-bot analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Bot Management Endpoints ─────────────────────────────────────────────────

@router.get("/bots", response_model=List[BotCapability])
async def list_bots() -> List[BotCapability]:
    """
    Get list of all available bots and their capabilities

    Returns information about each bot:
    - Name and description
    - Capabilities
    - Priority level
    - Current status
    """
    try:
        bots = []
        for bot_type, bot_info in unified_service.bot_capabilities.items():
            bots.append(BotCapability(
                bot_type=bot_type,
                name=bot_info['name'],
                description=bot_info['description'],
                capabilities=bot_info['capabilities'],
                priority=bot_info['priority'].value,
                status=bot_info['status']
            ))

        return bots

    except Exception as e:
        logger.error(f"Error listing bots: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/bots/{bot_type}")
async def get_bot_info(bot_type: BotType) -> Dict[str, Any]:
    """Get detailed information about a specific bot"""
    try:
        if bot_type not in unified_service.bot_capabilities:
            raise HTTPException(status_code=404, detail=f"Bot {bot_type} not found")

        bot_info = unified_service.bot_capabilities[bot_type]

        return {
            'bot_type': bot_type,
            'name': bot_info['name'],
            'description': bot_info['description'],
            'capabilities': bot_info['capabilities'],
            'priority': bot_info['priority'].value,
            'status': bot_info['status'],
            'use_cases': f"Best for: {', '.join(bot_info['capabilities'])}"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bot info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Routing Information Endpoint ─────────────────────────────────────────────

@router.post("/route")
async def simulate_routing(request: ChatMessage) -> Dict[str, Any]:
    """
    Simulate routing for a message without actually processing it

    Useful for understanding how messages will be routed and why.
    """
    try:
        # Get customer context
        customer_context = unified_service.get_customer_context(request.wa_id)

        # Determine routing
        bot_type = unified_service.route_message(request.message, request.wa_id, customer_context)

        return {
            'message': request.message,
            'wa_id': request.wa_id,
            'routed_to': bot_type,
            'routing_reason': unified_service.bot_capabilities.get(bot_type, {}).get('description', ''),
            'customer_context_summary': {
                'has_active_tickets': customer_context.get('support_context', {}).get('requires_attention', False),
                'is_sales_lead': customer_context.get('sales_context', {}).get('is_lead', False),
                'recent_bot_usage': customer_context.get('recent_interactions', {}).get('total_messages', 0)
            },
            'simulated_at': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error simulating routing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Health Check Endpoint ─────────────────────────────────────────────────────

@router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check for unified API and all bots"""
    try:
        bots_health = []
        all_healthy = True

        for bot_type, bot_info in unified_service.bot_capabilities.items():
            is_healthy = bot_info['status'] == 'active'
            health_info = BotHealth(
                bot_type=bot_type,
                status=bot_info['status'],
                uptime_percentage=100.0 if is_healthy else 0.0,
                avg_response_time_ms=150.0,  # Placeholder
                error_rate=0.0 if is_healthy else 100.0
            )
            bots_health.append(health_info)
            if not is_healthy:
                all_healthy = False

        return {
            'status': 'healthy' if all_healthy else 'degraded',
            'timestamp': datetime.now().isoformat(),
            'bots': bots_health,
            'total_bots': len(bots_health),
            'active_bots': sum(1 for b in bots_health if b.status == 'active')
        }

    except Exception as e:
        logger.error(f"Error in health check: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }

# ── Batch Operations Endpoints ────────────────────────────────────────────────

@router.post("/batch-context")
async def get_batch_context(wa_ids: List[str]) -> Dict[str, Any]:
    """Get customer context for multiple users at once"""
    try:
        contexts = {}
        for wa_id in wa_ids:
            try:
                contexts[wa_id] = unified_service.get_customer_context(wa_id)
            except Exception as e:
                contexts[wa_id] = {'error': str(e)}

        return {
            'total_requested': len(wa_ids),
            'successful_retrievals': sum(1 for c in contexts.values() if 'error' not in c),
            'contexts': contexts,
            'generated_at': datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error in batch context retrieval: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ── Export Endpoints ─────────────────────────────────────────────────────────

@router.get("/export/chat-history")
async def export_chat_history(
    wa_id: str,
    bot_type: Optional[BotType] = None,
    days: int = Query(30, description="Number of days to export", ge=1, le=365)
) -> Dict[str, Any]:
    """Export chat history for a user with optional bot type filtering"""
    try:
        from database import get_db_context

        with get_db_context() as conn:
            query = """SELECT route, message, response, created_at
                       FROM chat_history
                       WHERE wa_id = ?
                       AND created_at >= date('now', '-{} days')""".format(days)
            params = [wa_id]

            if bot_type:
                query += " AND route = ?"
                params.append(bot_type)

            query += " ORDER BY created_at ASC"

            rows = conn.execute(query, params).fetchall()

            return {
                'wa_id': wa_id,
                'bot_filter': bot_type,
                'time_range_days': days,
                'total_messages': len(rows),
                'messages': [dict(row) for row in rows],
                'exported_at': datetime.now().isoformat()
            }

    except Exception as e:
        logger.error(f"Error exporting chat history: {e}")
        raise HTTPException(status_code=500, detail=str(e))