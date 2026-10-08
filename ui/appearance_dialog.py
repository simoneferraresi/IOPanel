"""A focused dialog for previewing and choosing the application icon."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
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
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(10)

        title = QLabel("Choose the application icon", self)
        title.setObjectName("appearanceDialogHeading")
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        hint = QLabel("This changes the icon displayed while IOPanel is running.", self)
        hint.setObjectName("appearanceDialogDescription")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card_layout = QHBoxLayout()
        card_layout.setSpacing(16)
        card_layout.setContentsMargins(0, 4, 0, 4)
        layout.addLayout(card_layout, stretch=1)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.selection_buttons: dict[str, QToolButton] = {}
        self.card_previews: dict[str, QLabel] = {}
        self.card_labels: dict[str, QLabel] = {}
        self.card_label_rows: dict[str, QFrame] = {}
        self.selection_indicators: dict[str, QCheckBox] = {}
        for icon_id, display_name in APPLICATION_ICONS:
            button = QToolButton(self)
            button.setObjectName(f"applicationIconCard_{icon_id}")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setMinimumSize(250, 210)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            button.setToolTip(f"Select {display_name} as the application icon")
            button.setAccessibleName(display_name)
            button_layout = QVBoxLayout(button)
            button_layout.setContentsMargins(12, 12, 12, 12)
            button_layout.setSpacing(0)

            button_layout.addStretch(1)
            preview = QLabel(button)
            preview.setObjectName(f"applicationIconPreview_{icon_id}")
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setFixedSize(self.PREVIEW_SIZE, self.PREVIEW_SIZE)
            preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            pixmap_size = round(self.PREVIEW_SIZE * self.devicePixelRatioF())
            icon = application_icon(icon_id)
            source_size = max(pixmap_size, 256) if pixmap_size > self.PREVIEW_SIZE else pixmap_size
            pixmap = icon.pixmap(QSize(source_size, source_size))
            if source_size != pixmap_size:
                pixmap = pixmap.scaled(
                    QSize(pixmap_size, pixmap_size),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
            preview.setPixmap(pixmap)
            self.card_previews[icon_id] = preview
            button_layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignHCenter)
            button_layout.addSpacing(16)

            label_row_frame = QFrame(button)
            label_row_frame.setObjectName("applicationIconLabelRow")
            label_row_frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label_row_frame.setStyleSheet(
                "QFrame#applicationIconLabelRow { border: 1px solid transparent; border-radius: 4px; }"
                "QFrame#applicationIconLabelRow[keyboardFocus='true'] { border: 1px dashed palette(text); }"
            )
            self.card_label_rows[icon_id] = label_row_frame
            label_row = QHBoxLayout(label_row_frame)
            label_row.setContentsMargins(8, 3, 8, 3)
            label_row.setSpacing(8)
            label_row.addStretch(1)
            indicator = QCheckBox(button)
            indicator.setObjectName(f"applicationIconSelected_{icon_id}")
            indicator.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            indicator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            indicator.setAccessibleName(f"{display_name} selection indicator")
            self.selection_indicators[icon_id] = indicator
            label_row.addWidget(indicator)

            label = QLabel(display_name, button)
            label.setObjectName(f"applicationIconName_{icon_id}")
            label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.card_labels[icon_id] = label
            label_row.addWidget(label)
            label_row.addStretch(1)
            button_layout.addWidget(label_row_frame, alignment=Qt.AlignmentFlag.AlignHCenter)
            button_layout.addStretch(1)
            button.setStyleSheet(
                "QToolButton { border: 2px solid palette(mid); border-radius: 10px; "
                "padding: 12px; background: palette(base); color: palette(text); }"
                "QToolButton:hover { background: palette(alternate-base); }"
                "QToolButton:checked { border: 3px solid palette(highlight); "
                "background: palette(alternate-base); }"
            )
            self.button_group.addButton(button)
            self.selection_buttons[icon_id] = button
            button.installEventFilter(self)
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
        self.setTabOrder(self.selection_buttons["optical_burst"], self.selection_buttons["prism_spectrum"])
        self.selection_buttons[self._selected_icon_id].setFocus()

    def eventFilter(self, watched, event):
        for icon_id, button in self.selection_buttons.items():
            if watched is not button:
                continue
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
                target_id = None
                if icon_id == "optical_burst" and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    target_id = "prism_spectrum"
                elif icon_id == "prism_spectrum" and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    target_id = "optical_burst"
                if target_id is not None:
                    self.selection_buttons[target_id].setFocus()
                    return True
            if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                if event.type() == QEvent.Type.FocusIn:
                    self._set_keyboard_focus(icon_id, True)
                QTimer.singleShot(0, self._sync_keyboard_focus)
                break
        return super().eventFilter(watched, event)

    def _sync_keyboard_focus(self) -> None:
        focused = QApplication.focusWidget()
        for icon_id, button in self.selection_buttons.items():
            self._set_keyboard_focus(icon_id, focused is button)

    def _set_keyboard_focus(self, icon_id: str, has_focus: bool) -> None:
        row = self.card_label_rows[icon_id]
        if row.property("keyboardFocus") == has_focus:
            return
        row.setProperty("keyboardFocus", has_focus)
        row.style().unpolish(row)
        row.style().polish(row)
        row.update()

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
            button.setAccessibleDescription("Currently selected" if selected else "Not selected")
            self.selection_indicators[icon_id].setChecked(selected)
