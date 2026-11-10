"""Keep the shared tab area's minimum tied to its visible page.

QTabWidget normally incorporates the minimum size hints of *every* page. Once a
tall panel has been visited, that can prevent the vertical camera splitter from
shrinking even when the active panel's controls fit in much less space.
"""

from __future__ import annotations

from typing import override

from PySide6.QtCore import QSize, Slot
from PySide6.QtWidgets import QStyle, QTabWidget, QWidget


class ActivePageTabWidget(QTabWidget):
    """Respect the current page's controls without letting inactive pages pin height."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._refresh_minimum)

    @Slot(int)
    def _refresh_minimum(self, _index: int) -> None:
        self.updateGeometry()

    @override
    def minimumSizeHint(self) -> QSize:
        page = self.currentWidget()
        if page is None:
            return super().minimumSizeHint()

        page_minimum = page.minimumSizeHint().expandedTo(page.minimumSize())
        tabs_minimum = self.tabBar().minimumSizeHint()
        # Leave room for the native tab frame without depending on a particular
        # platform's exact Fusion/native style measurements.
        frame = self.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, None, self)
        border = max(2 * frame, 8)

        if self.tabPosition() in (QTabWidget.TabPosition.West, QTabWidget.TabPosition.East):
            return QSize(
                page_minimum.width() + tabs_minimum.width() + border,
                max(page_minimum.height(), tabs_minimum.height()) + border,
            )
        return QSize(
            max(page_minimum.width(), tabs_minimum.width()) + border,
            page_minimum.height() + tabs_minimum.height() + border,
        )
