"""
Metrics counters for B2C + B2B funnel tracking.

Gracefully degrades if prometheus_client is not installed.
"""
try:
    from prometheus_client import Counter, Histogram, Gauge

    # B2C funnel
    b2c_product_viewed = Counter("b2c_product_viewed_total", "Products viewed", ["product_id"])
    b2c_cart_add = Counter("b2c_cart_add_total", "Add-to-cart actions", ["product_id"])
    b2c_checkout_started = Counter("b2c_checkout_started_total", "Checkout initiated", ["source"])
    b2c_checkout_completed = Counter(
        "b2c_checkout_completed_total", "Orders placed", ["source", "payment_method"]
    )
    b2c_checkout_failed = Counter("b2c_checkout_failed_total", "Checkout failures", ["reason"])
    b2c_checkout_abandoned = Counter("b2c_checkout_abandoned_total", "TTL expirations", ["state"])
    b2c_order_value = Histogram(
        "b2c_order_value_inr", "Order value",
        buckets=[100, 250, 500, 1000, 2000, 5000, 10000],
    )
    b2c_duplicate_webhook = Counter("b2c_duplicate_webhook_total", "Duplicate webhooks blocked")
    b2c_duplicate_order_blocked = Counter(
        "b2c_duplicate_order_blocked_total", "Duplicate orders blocked"
    )
    b2c_price_changed = Counter("b2c_price_revalidation_changed_total", "Price changes at checkout")
    b2c_oos_at_checkout = Counter("b2c_price_revalidation_oos_total", "OOS at checkout")
    b2c_lock_retry = Counter("b2c_optimistic_lock_retry_total", "Lock retries")
    b2c_lock_failed = Counter("b2c_optimistic_lock_failed_total", "Lock failures")
    b2c_active_carts = Gauge("b2c_active_carts_total", "Non-empty carts")

    # Cart / checkout additions
    cart_expired_total = Counter("cart_expired_total", "Carts expired via TTL", ["user_type"])
    checkout_step_latency_seconds = Histogram(
        "checkout_step_latency_seconds", "Checkout step duration", ["step", "user_type"]
    )
    stock_decrement_failed_total = Counter(
        "stock_decrement_failed_total", "Stock decrement failures", ["product_id"]
    )

    # B2B funnel
    b2b_order_started = Counter("b2b_order_started_total", "B2B order flows initiated")
    b2b_order_completed = Counter("b2b_order_completed_total", "B2B orders placed")
    b2b_order_failed = Counter("b2b_order_failed_total", "B2B order failures", ["reason"])
    b2b_order_abandoned = Counter("b2b_order_abandoned_total", "B2B TTL expirations", ["state"])
    b2b_duplicate_order_blocked = Counter(
        "b2b_duplicate_order_blocked_total", "B2B duplicates blocked"
    )
    b2b_order_value = Histogram(
        "b2b_order_value_inr", "B2B order value",
        buckets=[1000, 5000, 10000, 25000, 50000, 100000],
    )

    # Shared
    webhook_duplicate_blocked = Counter(
        "webhook_duplicate_blocked_total", "Webhook dedups", ["user_type"]
    )
    optimistic_lock_retry = Counter(
        "optimistic_lock_retry_total", "Lock retries", ["user_type"]
    )

    _HAS_PROMETHEUS = True

except ImportError:
    _HAS_PROMETHEUS = False

    class _NoopCounter:
        def inc(self, *a, **kw): pass
        def labels(self, *a, **kw): return self

    class _NoopHistogram:
        def observe(self, *a, **kw): pass
        def labels(self, *a, **kw): return self

    class _NoopGauge:
        def set(self, *a, **kw): pass
        def labels(self, *a, **kw): return self

    # Create no-op singletons
    b2c_product_viewed = _NoopCounter()
    b2c_cart_add = _NoopCounter()
    b2c_checkout_started = _NoopCounter()
    b2c_checkout_completed = _NoopCounter()
    b2c_checkout_failed = _NoopCounter()
    b2c_checkout_abandoned = _NoopCounter()
    b2c_order_value = _NoopHistogram()
    b2c_duplicate_webhook = _NoopCounter()
    b2c_duplicate_order_blocked = _NoopCounter()
    b2c_price_changed = _NoopCounter()
    b2c_oos_at_checkout = _NoopCounter()
    b2c_lock_retry = _NoopCounter()
    b2c_lock_failed = _NoopCounter()
    b2c_active_carts = _NoopGauge()
    b2b_order_started = _NoopCounter()
    b2b_order_completed = _NoopCounter()
    b2b_order_failed = _NoopCounter()
    b2b_order_abandoned = _NoopCounter()
    b2b_duplicate_order_blocked = _NoopCounter()
    b2b_order_value = _NoopHistogram()
    webhook_duplicate_blocked = _NoopCounter()
    optimistic_lock_retry = _NoopCounter()
    cart_expired_total = _NoopCounter()
    checkout_step_latency_seconds = _NoopHistogram()
    stock_decrement_failed_total = _NoopCounter()
