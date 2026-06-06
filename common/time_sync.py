"""时间同步辅助.

教室端禁止使用本地时钟决策, 必须依赖管理端时间.
本模块提供:
- 单次时间获取 (HTTP HEAD + 响应头)
- 平滑时钟 (考虑 RTT, 漂移补偿)
- 健康状态评估
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class TimeSample:
    server_ts: int          # 管理端时间
    agent_ts_before: int    # 发送前本地时间
    agent_ts_after: int     # 收到后本地时间

    @property
    def rtt(self) -> int:
        return self.agent_ts_after - self.agent_ts_before

    @property
    def offset(self) -> int:
        """估计的本地-服务端时差 = server - local."""
        local_mid = (self.agent_ts_before + self.agent_ts_after) // 2
        return self.server_ts - local_mid


class SmoothedClock:
    """基于多次采样的平滑时钟, 抗抖动."""

    def __init__(self, max_samples: int = 8) -> None:
        self._samples: list[TimeSample] = []
        self._max = max_samples
        self._offset: int = 0
        self._consecutive_failures: int = 0

    def add_sample(self, sample: TimeSample) -> None:
        self._samples.append(sample)
        if len(self._samples) > self._max:
            self._samples.pop(0)
        # 用中位数 (而非均值) 抗离群值
        if self._samples:
            offsets = sorted(s.offset for s in self._samples)
            self._offset = offsets[len(offsets) // 2]

    def record_failure(self) -> None:
        self._consecutive_failures += 1

    def record_success(self) -> None:
        self._consecutive_failures = 0

    @property
    def offset(self) -> int:
        return self._offset

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def now(self) -> int:
        """返回估计的管理端当前时间."""
        return int(time.time()) + self._offset

    def drift_sec(self) -> int:
        """返回最近一次估计的漂移 (绝对值)."""
        return abs(self._offset)


def make_sample(server_ts: int, before: int, after: int) -> TimeSample:
    return TimeSample(server_ts=server_ts, agent_ts_before=before, agent_ts_after=after)
