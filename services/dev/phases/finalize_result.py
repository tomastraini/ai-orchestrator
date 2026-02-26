from __future__ import annotations

from services.dev.types.dev_graph_state import DevGraphState


def run(state: DevGraphState, graph_cls: type) -> DevGraphState:
    return graph_cls._finalize_result_impl(state)

