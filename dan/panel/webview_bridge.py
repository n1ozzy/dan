"""Panel webview bridge placeholder."""

from __future__ import annotations


class WebViewBridge:
    def send_intent(self, name: str, payload: dict[str, object]) -> None:
        raise NotImplementedError("panel bridge is not implemented yet")
