"""
Generic System Prompts for Multi-Domain Support Chatbot

This module provides dynamic system prompts that adapt to different
company types, industries, and domains.

Usage:
    from routing.prompts import get_system_prompt, get_strict_prompt
    
    # Get prompt for current configuration
    system_prompt = get_system_prompt()
    
    # Get prompt for specific company type
    it_prompt = get_system_prompt(company_type='it')
"""
import os
from typing import Dict, Optional

from routing.config import (
    BRAND_NAME,
    BRAND_TAGLINE,
    SUPPORT_EMAIL,
    SUPPORT_PHONE,
    BRAND_WEBSITE,
    SIGNATURE,
)


# ============================================================================
# PROMPT TEMPLATES
# ============================================================================

# Generic prompt template that works for any company
GENERIC_SYSTEM_PROMPT_TEMPLATE = """
You are a friendly, helpful assistant for {brand_name}, {brand_tagline}.

Your job is to answer the user's question using ONLY the information provided in the context.

🚨 STRICT FORMATTING RULES (MANDATORY):
- DO NOT WRITE PARAGRAPHS. PARAGRAPHS ARE STRICTLY FORBIDDEN.
- EVERY SINGLE LINE of your answer MUST start with a bullet point (`• `).
- Do NOT write an introductory sentence before the bullets.
- Do NOT write a closing paragraph after the bullets.
- Format the entire response strictly as a list of bullet points.

RESPONSE RULES:
- When the user asks about {domain_specific_queries}, provide COMPREHENSIVE and DETAILED information.
- Break down every detail, requirement, feature, and limitation into individual bullet points.
- Never include URLs unless explicitly provided in the context.
- Never tell the user to visit the website if the information is available in the context.
- Use 100-160 words.
- Cover important specifications, timelines, requirements, etc.

RESPONSE STRUCTURE ({tone} style):
• [Direct, {tone} answer to the main question + relevant emoji]
• [Key detail / specification / requirement in *bold*]
• [Additional features, benefits, or implementation details]
• [Any prerequisites, limitations, or important notes]
• [Friendly closing line inviting the next step / question]

GENERAL RULES:
- Read ALL context carefully before responding.
- Never invent, assume, or calculate information not found in the context.
- Use {tone} language appropriate for this {industry}.
- If information is missing, state it honestly in a single bullet point.

HUMAN SUPPORT TRIGGER:
If the user is frustrated, confused, asks for human support, or asks about {support_trigger_topics}:
• 🙋 **Need further assistance?** Our support team is happy to help!
• 📧 Email us at: {support_email}
• 📞 Call us at: {support_phone}
• 💬 We'll assist you and get back to you as soon as possible!

End EVERY response with this signature on its own line:
---
{signature}
"""


# Strict prompt template for low-confidence matches
STRICT_SYSTEM_PROMPT_TEMPLATE = """
You are a friendly, helpful assistant for {brand_name}, {brand_tagline}.

The retrieved context is a PARTIAL match to the user's question. Extract and combine ALL available relevant details.

🚨 STRICT FORMATTING RULES (MANDATORY):
- DO NOT WRITE PARAGRAPHS. PARAGRAPHS ARE STRICTLY FORBIDDEN.
- EVERY SINGLE LINE of your answer MUST start with a bullet point (`• `).
- Do NOT write an introductory sentence before the bullets.
- Do NOT write a closing paragraph after the bullets.
- Format the entire response strictly as a list of bullet points.

QUERY RULE:
- For questions on {domain_specific_topics}, extract and present ALL available conditions, requirements, features, and limitations in thorough bullet points.
- Keep all specific details, specifications, timelines, and requirements intact without skipping details.
- Add relevant emojis to points for readability.
- Use 100-160 words.
- Cover important specifications, timelines, requirements, etc.

RESPONSE STRUCTURE:
• [Direct answer based on the available context + emoji]
• [Specific detail, requirement, or partial answer with *bold* highlights]
• [Additional relevant context facts / features / specifications]
• [Honest note about missing details or offer to connect with support]

HUMAN SUPPORT TRIGGER:
If the query cannot be fully resolved, or involves {complex_topics}:
• 🙋 **Need assistance?** Our expert team is happy to help!
• 📧 Email us at: {support_email}
• 📞 Call us at: {support_phone}
• 💬 We're available to discuss your requirements!

End EVERY response with this signature on its own line:
---
{signature}
"""


# Listing prompt template
LISTING_SYSTEM_PROMPT_TEMPLATE = """
You are a friendly, helpful assistant for {brand_name}, {brand_tagline}.

The user asked to see a list of {listing_type}. The context is the COMPLETE data extracted from the company's information.

🚨 STRICT RULES (MANDATORY):
- List EVERY single item in the context. DO NOT omit any item.
- ONE bullet point (`• `) per item, each starting on a new line.
- For each item include: name, brief description, and any key specifications or prices.
- Never invent, guess, or calculate items, names, or specs that are not in the context.
- If the context contains no items, say so honestly in a single bullet.
- DO NOT write paragraphs. DO NOT write an introductory sentence or closing paragraph.
- This is a request for the full list: completeness matters MORE than word count. Ignore any generic 100-160 word guidance.

RESPONSE STRUCTURE:
• [Item 1 - name | brief description | key specs]
• [Item 2 - name | brief description | key specs]
... and so on for EVERY item ...
• [Friendly closing line inviting the next step]

End EVERY response with this signature on its own line:
---
{signature}
"""


# ============================================================================
# COMPANY TYPE CONFIGURATIONS
# ============================================================================

COMPANY_TYPE_CONFIGS: Dict[str, Dict] = {
    # IT / Technology Companies
    'it': {
        'name': 'IT/Technology',
        'tone': 'professional',
        'industry': 'technology',
        'domain_specific_queries': 'technical specifications, services, pricing, implementation, or development',
        'domain_specific_topics': 'services, pricing, technical specifications, implementation, or support',
        'support_trigger_topics': 'custom development quotes, enterprise solutions, technical troubleshooting, project timelines, pricing negotiations',
        'complex_topics': 'custom development requirements, enterprise architecture, complex integrations, project management',
        'listing_type': 'services or solutions',
        'keywords': ['software', 'development', 'technical', 'it', 'technology', 'solution', 'service'],
    },
    
    # Food / Restaurant Companies
    'food': {
        'name': 'Food/Restaurant',
        'tone': 'friendly',
        'industry': 'food and beverage',
        'domain_specific_queries': 'menu items, ingredients, pricing, delivery, or nutritional information',
        'domain_specific_topics': 'menu, pricing, delivery, ingredients, nutritional information',
        'support_trigger_topics': 'bulk orders, catering requests, dietary restrictions, allergy information',
        'complex_topics': 'custom menu requests, large catering orders, special dietary needs',
        'listing_type': 'menu items or dishes',
        'keywords': ['menu', 'food', 'dish', 'order', 'delivery', 'restaurant', 'cafe', 'catering'],
    },
    
    # E-commerce Companies
    'ecommerce': {
        'name': 'E-commerce',
        'tone': 'customer-friendly',
        'industry': 'retail',
        'domain_specific_queries': 'products, pricing, availability, shipping, or return policies',
        'domain_specific_topics': 'products, pricing, availability, shipping, delivery times, return policies',
        'support_trigger_topics': 'order tracking, product complaints, return requests, refund issues',
        'complex_topics': 'bulk orders, custom products, enterprise pricing, integration requests',
        'listing_type': 'products or catalog items',
        'keywords': ['product', 'price', 'order', 'shipping', 'delivery', 'cart', 'checkout', 'catalog'],
    },
    
    # Manufacturing Companies
    'manufacturing': {
        'name': 'Manufacturing',
        'tone': 'professional',
        'industry': 'manufacturing',
        'domain_specific_queries': 'product lines, specifications, pricing, distributors, or technical details',
        'domain_specific_topics': 'product lines, specifications, pricing, distributors, technical details, warranty',
        'support_trigger_topics': 'bulk ordering, custom manufacturing, technical specifications, distribution inquiries',
        'complex_topics': 'custom manufacturing requests, OEM inquiries, technical specifications, large volume orders',
        'listing_type': 'product lines or catalog items',
        'keywords': ['product', 'manufacturing', 'specification', 'technical', 'distributor', 'bulk', 'custom'],
    },
    
    # Healthcare Companies
    'healthcare': {
        'name': 'Healthcare',
        'tone': 'compassionate and professional',
        'industry': 'healthcare',
        'domain_specific_queries': 'services, appointments, doctors, treatments, or medical information',
        'domain_specific_topics': 'services, appointments, doctors, treatments, medical information, health tips',
        'support_trigger_topics': 'medical emergencies, appointment scheduling, doctor consultations, treatment inquiries',
        'complex_topics': 'complex medical conditions, treatment plans, specialist referrals',
        'listing_type': 'services or treatments',
        'keywords': ['appointment', 'doctor', 'treatment', 'medical', 'health', 'service', 'clinic', 'hospital'],
    },
    
    # Retail Companies
    'retail': {
        'name': 'Retail',
        'tone': 'customer-friendly',
        'industry': 'retail',
        'domain_specific_queries': 'products, pricing, availability, store locations, or promotions',
        'domain_specific_topics': 'products, pricing, availability, store locations, promotions, discounts',
        'support_trigger_topics': 'product availability, store locations, return requests, warranty claims',
        'complex_topics': 'bulk purchases, custom orders, store-specific inquiries',
        'listing_type': 'products or categories',
        'keywords': ['product', 'price', 'store', 'location', 'promotion', 'discount', 'retail'],
    },
    
    # Generic / Default
    'generic': {
        'name': 'Generic',
        'tone': 'helpful',
        'industry': 'business',
        'domain_specific_queries': 'products, services, pricing, or support',
        'domain_specific_topics': 'products, services, pricing, support, information',
        'support_trigger_topics': 'custom requests, complex inquiries, urgent issues',
        'complex_topics': 'custom requirements, enterprise needs, complex issues',
        'listing_type': 'products, services, or information',
        'keywords': ['product', 'service', 'price', 'support', 'information', 'help'],
    },
}


# ============================================================================
# PROMPT GENERATION FUNCTIONS
# ============================================================================

def get_profile_prompt_config(tenant_id: Optional[str] = None) -> Optional[Dict]:
    """Prompt config for a tenant, read from its published profile.

    Returns None when no profile is available, so the caller can fall back to
    COMPANY_TYPE_CONFIGS. That fallback is what keeps unmigrated entry points
    and the old eval baseline working.
    """
    try:
        from shared.tenancy import loader, store
    except ImportError:
        return None
    # Only a REGISTERED tenant gets profile-driven config. Without this check an
    # unknown string ("it", "food") would be coerced into a synthetic generic
    # profile and silently shadow the legacy COMPANY_TYPE_CONFIGS entry.
    if not store.get_tenant(tenant_id):
        return None
    try:
        profile = loader.get_tenant_profile(tenant_id)
    except Exception:
        return None
    return profile.prompt.model_dump()


def get_company_config(company_type: Optional[str] = None) -> Dict:
    """
    Get configuration for a specific company type.

    Resolution order:
      1. ``company_type`` that names a live tenant (e.g. 'troogood', 'leewaysoftech')
      2. ``COMPANY_TYPE`` env var, if it names a tenant
      3. ``COMPANY_TYPE`` env var as a legacy vertical key
      4. COMPANY_TYPE_CONFIGS['generic']

    Args:
        company_type: Tenant id, or a legacy company-type key

    Returns:
        Dictionary with company-specific configuration
    """
    if company_type is None:
        company_type = os.getenv("COMPANY_TYPE", "").strip()

    if company_type:
        profile_cfg = get_profile_prompt_config(company_type)
        if profile_cfg:
            return profile_cfg

    if not company_type:
        return COMPANY_TYPE_CONFIGS['generic']

    return COMPANY_TYPE_CONFIGS.get(company_type.lower(), COMPANY_TYPE_CONFIGS['generic'])


def get_system_prompt(
    company_type: Optional[str] = None,
    use_strict: bool = False
) -> str:
    """
    Generate a system prompt for the specified company type.
    
    Args:
        company_type: The company type (default: from COMPANY_TYPE env var)
        use_strict: If True, use the strict prompt template
        
    Returns:
        Formatted system prompt string
    """
    config = get_company_config(company_type)
    
    if use_strict:
        template = STRICT_SYSTEM_PROMPT_TEMPLATE
        complex_topics = config['complex_topics']
    else:
        template = GENERIC_SYSTEM_PROMPT_TEMPLATE
        complex_topics = config['support_trigger_topics']
    
    return template.format(
        brand_name=BRAND_NAME,
        brand_tagline=BRAND_TAGLINE,
        support_email=SUPPORT_EMAIL,
        support_phone=SUPPORT_PHONE,
        brand_website=BRAND_WEBSITE,
        signature=SIGNATURE,
        tone=config['tone'],
        industry=config['industry'],
        domain_specific_queries=config['domain_specific_queries'],
        domain_specific_topics=config['domain_specific_topics'],
        support_trigger_topics=config['support_trigger_topics'],
        complex_topics=complex_topics,
        listing_type=config['listing_type'],
    )


def get_strict_prompt(company_type: Optional[str] = None) -> str:
    """
    Get the strict system prompt for a company type.
    
    Args:
        company_type: The company type
        
    Returns:
        Strict system prompt string
    """
    return get_system_prompt(company_type, use_strict=True)


def get_listing_prompt(company_type: Optional[str] = None) -> str:
    """
    Get the listing system prompt for a company type.
    
    Args:
        company_type: The company type
        
    Returns:
        Listing system prompt string
    """
    config = get_company_config(company_type)
    
    return LISTING_SYSTEM_PROMPT_TEMPLATE.format(
        brand_name=BRAND_NAME,
        brand_tagline=BRAND_TAGLINE,
        signature=SIGNATURE,
        listing_type=config['listing_type'],
    )


def get_company_type_from_query(query: str) -> Optional[str]:
    """
    Attempt to detect company type from a query.
    
    Args:
        query: The user query
        
    Returns:
        Detected company type or None
    """
    query_lower = query.lower()
    
    # IT/Technology keywords
    it_keywords = ['software', 'development', 'it ', 'technology', 'tech ', 'coding', 'programming', 
                  'application', 'app ', 'system', 'server', 'cloud', 'api']
    if any(kw in query_lower for kw in it_keywords):
        return 'it'
    
    # Food keywords
    food_keywords = ['menu', 'food', 'dish', 'restaurant', 'cafe', 'order food', 
                    'cuisine', 'meal', 'recipe', 'catering', 'delivery']
    if any(kw in query_lower for kw in food_keywords):
        return 'food'
    
    # E-commerce keywords
    ecommerce_keywords = ['cart', 'checkout', 'ecommerce', 'online store', 'product ', 
                          'shop ', 'buy ', 'purchase', 'order ', 'shipping', 'catalog']
    if any(kw in query_lower for kw in ecommerce_keywords):
        return 'ecommerce'
    
    # Healthcare keywords
    healthcare_keywords = ['appointment', 'doctor', 'hospital', 'clinic', 'medical', 
                          'health', 'treatment', 'patient', 'diagnosis', 'therapy']
    if any(kw in query_lower for kw in healthcare_keywords):
        return 'healthcare'
    
    # Manufacturing keywords
    manufacturing_keywords = ['manufacturing', 'product line', 'industrial', 'bulk', 
                             'wholesale', 'distributor', 'oem', 'production']
    if any(kw in query_lower for kw in manufacturing_keywords):
        return 'manufacturing'
    
    # Retail keywords
    retail_keywords = ['retail', 'store ', 'shop ', 'outlet', 'branch', 'location']
    if any(kw in query_lower for kw in retail_keywords):
        return 'retail'
    
    return None


# ============================================================================
# COMPATIBILITY LAYER
# ============================================================================

# For backward compatibility with existing code
SYSTEM_PROMPT = get_system_prompt()
STRICT_SYSTEM_PROMPT = get_strict_prompt()
LISTING_SYSTEM_PROMPT = get_listing_prompt()


# Re-export for easy access
__all__ = [
    'GENERIC_SYSTEM_PROMPT_TEMPLATE',
    'STRICT_SYSTEM_PROMPT_TEMPLATE',
    'LISTING_SYSTEM_PROMPT_TEMPLATE',
    'COMPANY_TYPE_CONFIGS',
    'get_company_config',
    'get_system_prompt',
    'get_strict_prompt',
    'get_listing_prompt',
    'get_company_type_from_query',
    'SYSTEM_PROMPT',
    'STRICT_SYSTEM_PROMPT',
    'LISTING_SYSTEM_PROMPT',
]
