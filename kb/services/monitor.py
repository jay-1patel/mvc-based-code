import time
from typing import Dict, List
from collections import defaultdict
from dataclasses import dataclass, field

from routing.config import logger


@dataclass
class MetricCounter:
    total: int = 0
    success: int = 0
    failed: int = 0
    avg_duration_ms: float = 0.0
    _durations: list = field(default_factory=list)

    def record(self, success: bool, duration_ms: float):
        self.total += 1
        if success:
            self.success += 1
        else:
            self.failed += 1
        self._durations.append(duration_ms)
        if len(self._durations) > 1000:
            self._durations = self._durations[-500:]
        self.avg_duration_ms = sum(self._durations) / len(self._durations)


class Monitor:
    def __init__(self):
        self._counters: Dict[str, MetricCounter] = defaultdict(MetricCounter)
        self._start_time = time.time()
        self._errors: List[Dict] = []
        self._max_errors = 100

    def record(self, metric_name: str, success: bool, duration_ms: float = 0):
        self._counters[metric_name].record(success, duration_ms)

    def record_error(self, source: str, error: str, context: Dict = None):
        self._errors.append({
            "source": source,
            "error": error[:500],
            "context": context or {},
            "timestamp": time.time(),
        })
        if len(self._errors) > self._max_errors:
            self._errors = self._errors[-(self._max_errors // 2):]

    def get_stats(self, metric_name: str = None) -> Dict:
        if metric_name:
            counter = self._counters.get(metric_name)
            if not counter:
                return {"metric": metric_name, "total": 0}
            return {
                "metric": metric_name,
                "total": counter.total,
                "success": counter.success,
                "failed": counter.failed,
                "avg_duration_ms": round(counter.avg_duration_ms, 2),
                "success_rate": round(counter.success / max(1, counter.total) * 100, 1),
            }

        stats = {}
        for name, counter in self._counters.items():
            stats[name] = {
                "total": counter.total,
                "success": counter.success,
                "failed": counter.failed,
                "avg_duration_ms": round(counter.avg_duration_ms, 2),
            }
        return stats

    def get_recent_errors(self, limit: int = 10) -> List[Dict]:
        return self._errors[-limit:]

    def get_health(self) -> Dict:
        uptime = time.time() - self._start_time
        total_requests = sum(c.total for c in self._counters.values())
        total_failed = sum(c.failed for c in self._counters.values())
        return {
            "status": "healthy" if total_failed < total_requests * 0.1 else "degraded",
            "uptime_seconds": round(uptime, 0),
            "total_requests": total_requests,
            "total_failed": total_failed,
            "error_rate": round(total_failed / max(1, total_requests) * 100, 2),
            "recent_errors": len(self._errors),
        }


monitor = Monitor()


def track(metric_name: str):
    class _Tracker:
        def __init__(self):
            self._start = None

        def __enter__(self):
            self._start = time.time()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            duration_ms = (time.time() - self._start) * 1000
            success = exc_type is None
            monitor.record(metric_name, success, duration_ms)
            if not success:
                monitor.record_error(metric_name, str(exc_val))
            return False

    return _Tracker()
