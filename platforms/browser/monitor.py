"""自愈监控 — 从 Phantom self_healing.py 移植

追踪各平台/操作的成功率，下降时自动告警。
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Callable

logger = logging.getLogger(__name__)


class HealthMonitor:
    """滑动窗口健康监控"""

    def __init__(self, window_size: int = 50, success_threshold: float = 0.6):
        self._collectors: dict[str, deque] = {}
        self._window_size = window_size
        self._threshold = success_threshold
        self._alert_callbacks: list[Callable[[str, float], None]] = []

    def record(self, component: str, success: bool, duration_ms: int = 0):
        """记录一次操作结果"""
        if component not in self._collectors:
            self._collectors[component] = deque(maxlen=self._window_size)
        self._collectors[component].append(1 if success else 0)

        rate = self.success_rate(component)
        if rate < self._threshold and len(self._collectors[component]) >= 5:
            msg = f"[Monitor] {component} success rate LOW: {rate:.0%}"
            logger.warning(msg)
            for cb in self._alert_callbacks:
                try:
                    cb(component, rate)
                except Exception:
                    pass

    def success_rate(self, component: str) -> float:
        q = self._collectors.get(component)
        if not q:
            return 1.0
        return sum(q) / len(q)

    def on_alert(self, callback: Callable[[str, float], None]):
        self._alert_callbacks.append(callback)

    def summary(self) -> dict[str, dict]:
        return {
            comp: {
                "total": len(q),
                "success_rate": round(sum(q) / len(q) * 100, 1) if q else 100,
            }
            for comp, q in self._collectors.items()
        }


# 全局监控实例
health_monitor = HealthMonitor()
