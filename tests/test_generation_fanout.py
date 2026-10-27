"""Meeting generation SSE fan-out (2026-09-19 并发审查).

旧设计里第二个观看者直接 ``get()`` 生产者的同一个队列——token 被两个消费
者随机瓜分，两边内容都残缺。修复后每个订阅者拿自己的完整事件流。
"""

from __future__ import annotations

import queue
import threading

from src.meeting.generation import MeetingGenerationMixin


def _drain(q: queue.Queue, sink: list) -> None:
    while True:
        try:
            item = q.get(timeout=2)
        except queue.Empty:
            return
        if item[0] == "done":
            sink.append(item)
            return
        sink.append(item)


def test_two_subscribers_each_get_full_stream():
    fan = MeetingGenerationMixin._FanoutQueue()
    qa = fan.subscribe()
    qb = fan.subscribe()

    # 模拟 worker：推 20 个 token 再 done
    for i in range(20):
        fan.put(("token", str(i)))
    fan.put(("done", None))

    got_a, got_b = [], []
    ta = threading.Thread(target=_drain, args=(qa, got_a))
    tb = threading.Thread(target=_drain, args=(qb, got_b))
    ta.start(); tb.start(); ta.join(3); tb.join(3)

    tokens_a = [d for t, d in got_a if t == "token"]
    tokens_b = [d for t, d in got_b if t == "token"]
    # 修复前：两个消费者瓜分同一个队列，各拿 ~10 个、顺序随机
    assert tokens_a == [str(i) for i in range(20)]
    assert tokens_b == [str(i) for i in range(20)]


def test_subscriber_disconnect_stops_accumulation():
    fan = MeetingGenerationMixin._FanoutQueue()
    qa = fan.subscribe()
    fan.put(("token", "x"))
    assert qa.get(timeout=1) == ("token", "x")  # 退订前的正常投递
    fan.unsubscribe(qa)
    fan.put(("token", "y"))
    # 已退订的队列不再收到事件（不会无界积压）
    assert qa.empty()
    qa2 = fan.subscribe()
    fan.put(("token", "z"))
    assert qa2.get(timeout=1) == ("token", "z")


def test_late_subscriber_after_done_gets_done_immediately():
    """订阅落在 done 广播之后、登记清除之前的窗口 → 立刻拿到 done，不挂死。"""
    fan = MeetingGenerationMixin._FanoutQueue()
    fan.put(("token", "a"))
    fan.put(("done", None))
    late = fan.subscribe()
    item = late.get(timeout=1)
    assert item == ("done", None)


def test_worker_style_put_signature_compatible():
    """worker 里的 event_queue.put((t, d)) 无需任何改动即可广播。"""
    fan = MeetingGenerationMixin._FanoutQueue()
    q = fan.subscribe()
    fan.put(("state", {"summary": "prefilling"}))
    assert q.get(timeout=1) == ("state", {"summary": "prefilling"})
