"""Fake Appium driver and elements for deterministic offline unit tests."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw


@dataclass
class FakeElement:
    key: tuple[str, str]
    text: str = ""
    rect: dict[str, int] = field(default_factory=lambda: {"x": 20, "y": 40, "width": 100, "height": 30})
    displayed: bool = True
    clicks: int = 0
    typed: list[str] = field(default_factory=list)
    cleared: int = 0

    def click(self) -> None:
        self.clicks += 1

    def send_keys(self, value: str) -> None:
        self.typed.append(value)
        self.text = value

    def clear(self) -> None:
        self.cleared += 1
        self.text = ""

    def is_displayed(self) -> bool:
        return self.displayed


class FakeDriver:
    """In-memory fake implementing DriverProtocol with Retina 2x PNG screenshots."""

    is_fake = True

    def __init__(
        self,
        platform: str = "android",
        window_size: dict[str, int] | None = None,
        screenshot_scale: int = 2,
    ) -> None:
        self.platform = platform
        self._window_size = window_size or {"width": 390, "height": 844}
        self._scale = screenshot_scale
        self.elements: dict[tuple[str, str], list[FakeElement]] = {}
        self.delayed_elements: dict[tuple[str, str], tuple[int, list[FakeElement]]] = {}
        self.disappear_after: dict[tuple[str, str], int] = {}
        self._lookup_counts: dict[tuple[str, str], int] = {}
        self._page_source = "<hierarchy><android.widget.FrameLayout /></hierarchy>"
        self.capabilities: dict[str, Any] = {"platformVersion": "14"}
        self.calls: list[tuple[str, Any]] = []
        self.quit_called = False
        self.secret_paint_boxes: list[dict[str, int]] = []
        self.on_swipe_hook: Any = None

    def set_page_source(self, xml: str) -> None:
        self._page_source = xml

    @property
    def page_source(self) -> str:
        return self._page_source

    def add_element(
        self,
        strategy: str,
        value: str,
        text: str = "",
        rect: dict[str, int] | None = None,
        displayed: bool = True,
    ) -> FakeElement:
        key = (strategy, value)
        el = FakeElement(
            key=key,
            text=text,
            rect=rect or {"x": 20, "y": 40, "width": 100, "height": 30},
            displayed=displayed,
        )
        self.elements.setdefault(key, []).append(el)
        return el

    def appear_after(
        self,
        strategy: str,
        value: str,
        calls: int,
        text: str = "",
        rect: dict[str, int] | None = None,
    ) -> FakeElement:
        key = (strategy, value)
        el = FakeElement(
            key=key,
            text=text,
            rect=rect or {"x": 20, "y": 40, "width": 100, "height": 30},
            displayed=True,
        )
        self.delayed_elements[key] = (calls, [el])
        return el

    def find_elements(self, strategy: str, value: str) -> list[FakeElement]:
        key = (strategy, value)
        self._lookup_counts[key] = self._lookup_counts.get(key, 0) + 1
        count = self._lookup_counts[key]

        if key in self.disappear_after and count >= self.disappear_after[key]:
            return []

        if key in self.delayed_elements:
            threshold, elems = self.delayed_elements[key]
            if count >= threshold:
                self.elements[key] = elems
                del self.delayed_elements[key]
                return elems
            return []

        return list(self.elements.get(key, []))

    def find_element(self, strategy: str, value: str) -> FakeElement:
        elems = self.find_elements(strategy, value)
        if not elems:
            raise LookupError(f"Element not found: {strategy}={value!r}")
        return elems[0]

    def get_window_size(self) -> dict[str, int]:
        return dict(self._window_size)

    def get_screenshot_as_png(self) -> bytes:
        w = self._window_size["width"] * self._scale
        h = self._window_size["height"] * self._scale
        img = Image.new("RGB", (w, h), color=(240, 245, 250))
        draw = ImageDraw.Draw(img)
        # Paint bright red inside any designated secret boxes so tests can prove Pillow black-fills them
        for box in self.secret_paint_boxes:
            x0 = box["x"] * self._scale
            y0 = box["y"] * self._scale
            x1 = (box["x"] + box["width"]) * self._scale
            y1 = (box["y"] + box["height"]) * self._scale
            draw.rectangle([x0, y0, x1, y1], fill=(255, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def activate_app(self, app_id: str) -> None:
        self.calls.append(("activate_app", app_id))

    def terminate_app(self, app_id: str) -> None:
        self.calls.append(("terminate_app", app_id))

    def remove_app(self, app_id: str) -> None:
        self.calls.append(("remove_app", app_id))

    def install_app(self, app_path: str) -> None:
        self.calls.append(("install_app", app_path))

    def background_app(self, seconds: int | float) -> None:
        self.calls.append(("background_app", seconds))

    def back(self) -> None:
        self.calls.append(("back", None))

    def hide_keyboard(self) -> None:
        self.calls.append(("hide_keyboard", None))

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 300) -> None:
        self.calls.append(("swipe", (start_x, start_y, end_x, end_y, duration)))
        if self.on_swipe_hook:
            self.on_swipe_hook(self)

    def execute_script(self, script: str, *args: Any) -> Any:
        self.calls.append(("execute_script", (script, args)))
        return None

    def quit(self) -> None:
        self.quit_called = True
        self.calls.append(("quit", None))
