"""A focused dialog for previewing and choosing the application icon."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from ui.application_icons import APPLICATION_ICON_IDS, APPLICATION_ICONS, application_icon


class AppearanceDialog(QDialog):
    """Preview the supported app icons and return a choice only on Apply."""

    PREVIEW_SIZE = 128

    def __init__(self, current_icon_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.setAccessibleName("Appearance settings")
        self._selected_icon_id = current_icon_id if current_icon_id in APPLICATION_ICON_IDS else "optical_burst"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = QLabel("Appearance", self)
        title.setObjectName("appearanceDialogTitle")
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        section = QLabel("Application icon", self)
        section.setObjectName("applicationIconSectionTitle")
        section_font = QFont(section.font())
        section_font.setBold(True)
        section.setFont(section_font)
        layout.addWidget(section)

        hint = QLabel("Choose the icon shown by IOPanel while it is running.", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card_layout = QHBoxLayout()
        card_layout.setSpacing(16)
        card_layout.setContentsMargins(0, 4, 0, 4)
        layout.addLayout(card_layout, stretch=1)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.selection_buttons: dict[str, QToolButton] = {}
        for icon_id, display_name in APPLICATION_ICONS:
            button = QToolButton(self)
            button.setObjectName(f"applicationIconCard_{icon_id}")
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIcon(application_icon(icon_id))
            button.setIconSize(QSize(self.PREVIEW_SIZE, self.PREVIEW_SIZE))
            button.setMinimumSize(250, 232)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            button.setToolTip(f"Select {display_name} as the application icon")
            button.setAccessibleName(display_name)
            button.setStyleSheet(
                "QToolButton { border: 2px solid palette(mid); border-radius: 10px; "
                "padding: 12px; background: palette(base); color: palette(text); }"
                "QToolButton:hover { background: palette(alternate-base); }"
                "QToolButton:checked { border: 3px solid palette(highlight); "
                "background: palette(alternate-base); }"
                "QToolButton:focus { border-style: dashed; }"
            )
            self.button_group.addButton(button)
            self.selection_buttons[icon_id] = button
            button.clicked.connect(lambda _checked=False, selected=icon_id: self._select_icon(selected))
            card_layout.addWidget(button, stretch=1)

        self._update_card_labels()

        self.button_box = QDialogButtonBox(self)
        self.apply_button: QPushButton = self.button_box.addButton(QDialogButtonBox.StandardButton.Apply)
        self.cancel_button: QPushButton = self.button_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button.setObjectName("appearanceApplyButton")
        self.cancel_button.setObjectName("appearanceCancelButton")
        self.apply_button.clicked.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box, alignment=Qt.AlignmentFlag.AlignRight)
        self.setMinimumSize(600, layout.sizeHint().height())
        self.resize(700, max(460, layout.sizeHint().height()))

    @property
    def selected_icon_id(self) -> str:
        """Return the provisional identifier; callers should use it only on accept."""
        return self._selected_icon_id

    def _select_icon(self, icon_id: str) -> None:
        if icon_id not in APPLICATION_ICON_IDS:
            return
        self._selected_icon_id = icon_id
        self.selection_buttons[icon_id].setChecked(True)
        self._update_card_labels()

    def _update_card_labels(self) -> None:
        for icon_id, display_name in APPLICATION_ICONS:
            selected = icon_id == self._selected_icon_id
            button = self.selection_buttons[icon_id]
            button.setChecked(selected)
            button.setText(f"Selected: {display_name}" if selected else display_name)
            button.setAccessibleDescription("Currently selected" if selected else "Not selected")
