"""
Global and component-specific stylesheets for the Lab Application.

Defines the visual appearance of various UI elements using Qt Style Sheets (QSS).
Includes a global stylesheet (APP_STYLESHEET) and specific styles for custom panels
like the Camera Panel (CAMERA_PANEL_STYLE) and CT400 Control Panel (CT400_CONTROL_PANEL_STYLE).
"""

# Global application stylesheet for general widgets
APP_STYLESHEET = """
/* ----------------------------------------
   Design System Variables (Conceptual)
-----------------------------------------
   NOTE: QSS does not support CSS variables (`var(...)`).
   These are defined here for clarity and maintainability.
   Use find/replace or consider a QSS pre-processor (like qtsass)
   if extensive theming or easier refactoring is needed.
----------------------------------------- */
:root {
    /* Primary color palette */
    --primary-50: #eff6ff;
    --primary-100: #dbeafe;
    --primary-200: #bfdbfe;
    --primary-300: #93c5fd;
    --primary-400: #60a5fa;
    --primary-500: #3b82f6;
    --primary-600: #2563eb;
    --primary-700: #1d4ed8;
    --primary-800: #1e40af;
    --primary-900: #1e3a8a;

    /* Neutral color palette */
    --neutral-50: #f9fafb;
    --neutral-100: #f3f4f6;
    --neutral-200: #e5e7eb;
    --neutral-300: #d1d5db;
    --neutral-400: #9ca3af;
    --neutral-500: #6b7280;
    --neutral-600: #4b5563;
    --neutral-700: #374151;
    --neutral-800: #1f2937;
    --neutral-900: #111827;

    /* Success and error states */
    --success: #10b981;
    --success-light: #d1fae5;
    --error: #ef4444;
    --error-light: #fee2e2;
    --warning: #f59e0b;
    --warning-light: #fef3c7;

    /* Spacing system */
    --space-xs: 4px;
    --space-sm: 8px;
    --space-md: 12px;
    --space-lg: 16px;
    --space-xl: 24px;

    /* Font sizes */
    --font-xs: 12px;
    --font-sm: 14px;
    --font-md: 16px;
    --font-lg: 18px;
    --font-xl: 20px;

    /* Border radius */
    --radius-sm: 4px;
    --radius-md: 6px;
    --radius-lg: 8px;
    --radius-full: 9999px; /* Used for pills/badges */
}

/* ----------------------------------------
   General Widget Styling
----------------------------------------- */
QWidget {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: var(--font-sm); /* 14px */
    /* Default background for most widgets.
       Consider 'transparent' if issues arise with nested layout widgets
       unexpectedly inheriting a white background. */
    background-color: white;
    color: var(--neutral-800); /* #1f2937 */
}

/* ----------------------------------------
   Label Styling
----------------------------------------- */
QLabel {
    font-weight: 500;
    color: var(--neutral-700); /* #374151 */
    padding: var(--space-xs); /* 4px */
    background: none; /* Labels should generally be transparent */
    min-height: 20px; /* Adjust as needed based on typical font size */
}

QLabel.title {
    font-size: var(--font-md); /* 16px */
    font-weight: 600;
    color: var(--neutral-800); /* #1f2937 */
    padding: var(--space-sm) 0; /* 8px 0 */
}

QLabel.subtitle {
    font-size: var(--font-sm); /* 14px */
    font-weight: 500;
    color: var(--neutral-600); /* #4b5563 */
}

QLabel.badge {
    font-size: var(--font-xs); /* 12px */
    font-weight: 500;
    padding: 2px 10px; /* Specific padding for badge look */
    border-radius: 12px; /* Closer to var(--radius-full) for pill shape */
    background-color: var(--neutral-200); /* #e5e7eb */
    color: var(--neutral-700); /* #374151 */
    qproperty-alignment: AlignCenter;
}

QLabel.badge-success {
    background-color: var(--success-light); /* #d1fae5 */
    color: #065f46; /* Specific darker shade for contrast */
}

QLabel.badge-warning {
    background-color: var(--warning-light); /* #fef3c7 */
    color: #92400e; /* Specific darker shade for contrast */
}

QLabel.badge-error {
    background-color: var(--error-light); /* #fee2e2 */
    color: #b91c1c; /* Specific darker shade for contrast */
}

/* ----------------------------------------
   Group Box Styling
----------------------------------------- */
QGroupBox {
    font-weight: 600;
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-lg); /* 8px */
    margin-top: 20px; /* Space for title */
    padding-top: 16px; /* Internal padding below title */
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: var(--space-lg); /* 16px */
    padding: 0 var(--space-sm); /* 0 8px */
    color: var(--neutral-600); /* #4b5563 */
    /* background-color: white; */ /* Optional: If needed to mask border */
}

/* ----------------------------------------
   Input Fields: QLineEdit and QTextEdit
----------------------------------------- */
QLineEdit, QTextEdit {
    padding: 10px 12px; /* Consider var(--space-sm) var(--space-md) if suitable */
    font-size: var(--font-sm); /* 14px */
    border: 1px solid var(--neutral-300); /* #d1d5db */
    border-radius: var(--radius-md); /* 6px */
    background: white;
    color: var(--neutral-800); /* #1f2937 */
    selection-background-color: var(--primary-200); /* #bfdbfe */
}

QLineEdit:focus, QTextEdit:focus {
    border: 2px solid var(--primary-500); /* #3b82f6 */
    /* Adjust padding slightly to prevent layout shift due to thicker border */
    /* padding: 9px 11px; */ /* Uncomment if needed */
    background-color: var(--neutral-50); /* #f9fafb */
}

QLineEdit:hover:!focus, QTextEdit:hover:!focus {
    border: 1px solid var(--neutral-400); /* #9ca3af */
}

QLineEdit:disabled, QTextEdit:disabled {
    background-color: var(--neutral-100); /* #f3f4f6 */
    color: var(--neutral-400); /* #9ca3af */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
}

QLineEdit[readOnly="true"] {
    background-color: var(--neutral-100); /* #f3f4f6 */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
}

/* ----------------------------------------
   Combo Box Styling
----------------------------------------- */
QComboBox {
    padding: 10px 36px 10px 12px; /* Right padding accommodates arrow */
    font-size: var(--font-sm); /* 14px */
    border: 1px solid var(--neutral-300); /* #d1d5db */
    border-radius: var(--radius-md); /* 6px */
    background: white;
    color: var(--neutral-800); /* #1f2937 */
    min-height: 20px; /* Ensure minimum height */
}

QComboBox:focus {
    border: 2px solid var(--primary-500); /* #3b82f6 */
    /* padding: 9px 35px 9px 11px; */ /* Adjust padding if border shifts layout */
}

QComboBox:hover:!focus {
    border: 1px solid var(--neutral-400); /* #9ca3af */
}

QComboBox:disabled {
    background-color: var(--neutral-100); /* #f3f4f6 */
    color: var(--neutral-400); /* #9ca3af */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px; /* Width of the dropdown area */
    border-left: none; /* Avoid double border */
    border-top-right-radius: var(--radius-md); /* 6px */
    border-bottom-right-radius: var(--radius-md); /* 6px */
}

QComboBox::down-arrow {
    /* Ensure ':/icons/chevron-down.svg' exists in your compiled .qrc file */
    image: url(:/icons/chevron-down.svg);
    width: 16px;
    height: 16px;
}

QComboBox QAbstractItemView { /* Style for the dropdown list */
    background: white;
    border: 1px solid var(--neutral-300); /* #d1d5db */
    border-radius: var(--radius-md); /* 6px */
    selection-background-color: var(--primary-50); /* #eff6ff */
    selection-color: var(--primary-800); /* #1e40af */
    padding: var(--space-xs); /* 4px */
    outline: none; /* Remove focus rectangle around dropdown */
}

/* ----------------------------------------
   Button Styling
----------------------------------------- */
QPushButton {
    background: var(--primary-500); /* #3b82f6 */
    color: white;
    border: none;
    padding: 10px var(--space-lg); /* 10px 16px */
    border-radius: var(--radius-md); /* 6px */
    font-weight: 500;
    min-height: 38px; /* Consistent clickable height */
    min-width: 80px; /* Ensure some minimum width */
}

QPushButton:hover {
    background: var(--primary-600); /* #2563eb */
}

QPushButton:pressed {
    background: var(--primary-700); /* #1d4ed8 */
}

QPushButton:disabled {
    background: var(--neutral-200); /* #e5e7eb */
    color: var(--neutral-400); /* #9ca3af */
}

QPushButton.secondary {
    background: var(--neutral-100); /* #f3f4f6 */
    color: var(--neutral-600); /* #4b5563 */
    border: 1px solid var(--neutral-300); /* #d1d5db */
}

QPushButton.secondary:hover {
    background: var(--neutral-200); /* #e5e7eb */
    color: var(--neutral-800); /* #1f2937 */
}

QPushButton.secondary:pressed {
    background: var(--neutral-300); /* #d1d5db */
}

QPushButton.destructive {
    background: var(--error-light); /* #fee2e2 */
    color: #b91c1c; /* Specific dark red */
    border: 1px solid #fecaca; /* Light red border */
}

QPushButton.destructive:hover {
    background: #fecaca; /* Lighter red */
    color: #991b1b; /* Darker red */
}

QPushButton.small {
    padding: 6px var(--space-md); /* 6px 12px */
    font-size: var(--font-xs); /* 12px */
    min-height: 28px;
    min-width: 60px;
}

QPushButton.icon {
    padding: var(--space-sm); /* 8px */
    min-width: 38px; /* Square-ish size */
    min-height: 38px;
}

/* ----------------------------------------
   Checkbox and Radio Button Styling
----------------------------------------- */
QCheckBox, QRadioButton {
    spacing: var(--space-sm); /* 8px */
    color: var(--neutral-700); /* #374151 */
}

QCheckBox:disabled, QRadioButton:disabled {
    color: var(--neutral-400); /* #9ca3af */
}

QCheckBox::indicator, QRadioButton::indicator {
    width: 18px;
    height: 18px;
}

QCheckBox::indicator:unchecked {
    border: 2px solid var(--neutral-300); /* #d1d5db */
    border-radius: var(--radius-sm); /* 4px */
    background-color: white;
}

QCheckBox::indicator:unchecked:hover {
    border: 2px solid var(--neutral-400); /* #9ca3af */
}

QCheckBox::indicator:checked {
    border: 2px solid var(--primary-500); /* #3b82f6 */
    border-radius: var(--radius-sm); /* 4px */
    background-color: var(--primary-500); /* #3b82f6 */
    /* Ensure ':/icons/check.svg' exists in your compiled .qrc file and is suitable */
    image: url(:/icons/check.svg);
}

QCheckBox::indicator:checked:disabled {
    border: 2px solid var(--neutral-300); /* #d1d5db */
    background-color: var(--neutral-300); /* #d1d5db */
    /* Add disabled check icon if needed */
}

QRadioButton::indicator:unchecked {
    border: 2px solid var(--neutral-300); /* #d1d5db */
    border-radius: 10px; /* Round */
    background-color: white;
}

QRadioButton::indicator:unchecked:hover {
    border: 2px solid var(--neutral-400); /* #9ca3af */
}

QRadioButton::indicator:checked {
    border: 2px solid var(--primary-500); /* #3b82f6 */
    border-radius: 10px; /* Round */
    background-color: white; /* Background for the inner dot */
    /* Ensure ':/icons/radio-checked.svg' exists in your compiled .qrc file */
    image: url(:/icons/radio-checked.svg); /* This should be the inner dot image */
}

QRadioButton::indicator:checked:disabled {
    border: 2px solid var(--neutral-300); /* #d1d5db */
    background-color: var(--neutral-100); /* #f3f4f6 */
    /* Add disabled radio dot icon if needed */
    image: none; /* Or a specific disabled dot */
}


/* ----------------------------------------
   Slider Styling
----------------------------------------- */
QSlider::groove:horizontal {
    border: none;
    height: 8px;
    background: var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-sm); /* 4px */
}

QSlider::handle:horizontal {
    background: var(--primary-500); /* #3b82f6 */
    border: none;
    width: 18px;
    height: 18px;
    margin: -5px 0; /* Vertically center handle on groove */
    border-radius: 9px; /* Round handle */
}

QSlider::handle:horizontal:hover {
    background: var(--primary-600); /* #2563eb */
}

QSlider::sub-page:horizontal { /* Style for the part before the handle */
    background: var(--primary-300); /* #93c5fd */
    border-radius: var(--radius-sm); /* 4px */
}

/* ----------------------------------------
   Progress Bar Styling
----------------------------------------- */
QProgressBar {
    border: none;
    background: var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-sm); /* 4px */
    text-align: center;
    color: var(--neutral-700); /* #374151 - Default text color, visible */
    height: 8px;
}

/* Optional class to hide the percentage text if needed */
QProgressBar.no-text {
    color: transparent;
}

QProgressBar::chunk {
    background-color: var(--primary-500); /* #3b82f6 */
    border-radius: var(--radius-sm); /* 4px */
}

/* ----------------------------------------
   Tab Widget Styling
----------------------------------------- */
QTabWidget::pane { /* The area where tab content is shown */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-lg); /* 8px */
    /* Shift pane down slightly to connect visually with selected tab */
    top: -1px;
    background: white; /* Ensure pane background is white */
}

QTabBar::tab {
    background: var(--neutral-50); /* #f9fafb */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-bottom: none; /* Connects to pane border */
    border-top-left-radius: var(--radius-md); /* 6px */
    border-top-right-radius: var(--radius-md); /* 6px */
    padding: var(--space-sm) var(--space-lg); /* 8px 16px */
    margin-right: var(--space-xs); /* 4px */
    color: var(--neutral-500); /* #6b7280 */
}

QTabBar::tab:selected {
    background: white; /* Match pane background */
    color: var(--neutral-800); /* #1f2937 */
    font-weight: 500;
    /* border-bottom: 1px solid white; */ /* Hide bottom border by matching background */
}

QTabBar::tab:!selected {
    margin-top: 2px; /* Slightly lower unselected tabs */
}

QTabBar::tab:hover:!selected {
    background: var(--neutral-100); /* #f3f4f6 */
    color: var(--neutral-700); /* #374151 */
}

/* ----------------------------------------
   Scroll Area Styling
----------------------------------------- */
QScrollArea {
    background: transparent; /* Usually want the scroll area itself transparent */
    border: none;
}

/* Ensure the widget *inside* the QScrollArea has a background if needed */
QScrollArea > QWidget > QWidget {
     background: white; /* Or transparent depending on content */
}


/* ----------------------------------------
   Scroll Bars Styling
----------------------------------------- */
QScrollBar:vertical {
    background: var(--neutral-50); /* #f9fafb */
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: var(--neutral-300); /* #d1d5db */
    min-height: 30px;
    border-radius: 6px; /* Rounded handle */
}

QScrollBar::handle:vertical:hover {
    background: var(--neutral-400); /* #9ca3af */
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    /* Hide the top/bottom arrows */
    height: 0px;
    border: none;
    background: none;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    /* Background of the track */
    background: none;
}

QScrollBar:horizontal {
    background: var(--neutral-50); /* #f9fafb */
    height: 12px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: var(--neutral-300); /* #d1d5db */
    min-width: 30px;
    border-radius: 6px; /* Rounded handle */
}

QScrollBar::handle:horizontal:hover {
    background: var(--neutral-400); /* #9ca3af */
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    /* Hide the left/right arrows */
    width: 0px;
    border: none;
    background: none;
}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    /* Background of the track */
    background: none;
}


/* ----------------------------------------
   Status Bar Styling
----------------------------------------- */
QStatusBar {
    background: var(--neutral-50); /* #f9fafb */
    border-top: 1px solid var(--neutral-200); /* #e5e7eb */
    color: var(--neutral-500); /* #6b7280 */
}

QStatusBar::item {
    border: none; /* No borders around status bar sections */
}

/* ----------------------------------------
   Menu Styling
----------------------------------------- */
QMenuBar {
    background-color: var(--neutral-50); /* #f9fafb */
    border-bottom: 1px solid var(--neutral-200); /* #e5e7eb */
}

QMenuBar::item {
    padding: var(--space-sm) var(--space-md); /* 8px 12px */
    background: transparent;
}

QMenuBar::item:selected { /* Hover state */
    background: var(--primary-50); /* #eff6ff */
    border-radius: var(--radius-sm); /* 4px */
}

QMenuBar::item:pressed { /* When menu is open */
    background: var(--primary-100); /* #dbeafe */
}

QMenu {
    background-color: white;
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-md); /* 6px */
    padding: var(--space-sm) 0; /* 8px 0 */
}

QMenu::item {
    padding: 8px 32px 8px 16px; /* Space for checkmark/icon and text */
    color: var(--neutral-700); /* #374151 */
}

QMenu::item:selected { /* Hover/active state */
    background: var(--primary-50); /* #eff6ff */
    color: var(--primary-800); /* #1e40af */
}

QMenu::separator {
    height: 1px;
    background: var(--neutral-200); /* #e5e7eb */
    margin: var(--space-xs) 0; /* 4px 0 */
}

/* ----------------------------------------
   Tooltip Styling
----------------------------------------- */
QToolTip {
    background-color: var(--neutral-800); /* #1f2937 */
    color: white;
    border: none;
    border-radius: var(--radius-sm); /* 4px */
    padding: 6px 10px;
    opacity: 220; /* Qt specific opacity */
}

/* ----------------------------------------
   Frame Styling (Generic & Custom)
----------------------------------------- */
QFrame {
    /* Default frame has no border or background */
    border: none;
    background: transparent;
}

QFrame.card {
    background: white;
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-lg); /* 8px */
    padding: var(--space-lg); /* 16px */
}

QFrame.separator {
    background: var(--neutral-200); /* #e5e7eb */
}

/* Need to use property selector for orientation */
QFrame.separator[orientation="1"] { /* QFrame.HLine == 1 */
    height: 1px;
    min-height: 1px;
    max-height: 1px;
}

QFrame.separator[orientation="2"] { /* QFrame.VLine == 2 */
    width: 1px;
    min-width: 1px;
    max-width: 1px;
}
"""

# Stylesheet specific to the camera panel
CAMERA_PANEL_STYLE = """
/* ----------------------------------------
   Camera Panel Container
----------------------------------------- */
/* Apply using setObjectName("camera-panel") or a dynamic property */
QFrame#camera-panel, QFrame[panelType="camera"] {
    background: white;
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-lg); /* 8px */
    padding: var(--space-lg); /* 16px */
}

/* ----------------------------------------
   Camera Display Area
----------------------------------------- */
QLabel.camera-view {
    background-color: var(--neutral-100); /* #f3f4f6 */
    border-radius: var(--radius-lg); /* 8px */
    min-height: 240px; /* Example minimum height */
    qproperty-alignment: AlignCenter;
    color: var(--neutral-400); /* #9ca3af - For placeholder text like 'No Signal' */
}

/* ----------------------------------------
   Title Label in Camera Panel
----------------------------------------- */
QLabel.title-label { /* Use this class for titles within the panel */
    font-size: var(--font-md); /* 16px */
    font-weight: 600;
    color: var(--neutral-800); /* #1f2937 */
    margin-bottom: var(--space-sm); /* 8px */
    padding: 0; /* Override default QLabel padding if needed */
}

/* ----------------------------------------
   Status Indicator in Camera Panel
----------------------------------------- */
QLabel.camera-status { /* Use this class for status text */
    font-size: var(--font-xs); /* 12px */
    padding: 4px 12px; /* Specific padding */
    border-radius: var(--radius-full); /* 12px or more for pill shape */
    qproperty-alignment: AlignCenter;
    font-weight: 500;
}

/* Use dynamic properties to set the status */
QLabel.camera-status[status="active"] {
    background-color: var(--success-light); /* #d1fae5 */
    color: #065f46; /* Dark green */
}

QLabel.camera-status[status="standby"] {
    background-color: var(--warning-light); /* #fef3c7 */
    color: #92400e; /* Dark amber */
}

QLabel.camera-status[status="offline"] {
    background-color: var(--error-light); /* #fee2e2 */
    color: #b91c1c; /* Dark red */
}

/* ----------------------------------------
   Buttons in Camera Panel
----------------------------------------- */
QPushButton.camera-control { /* Specific button style for this panel */
    background: var(--primary-50); /* #eff6ff */
    color: var(--primary-800); /* #1e40af */
    padding: 8px 12px; /* Slightly smaller padding */
    border-radius: var(--radius-md); /* 6px */
    font-weight: 500;
    border: 1px solid var(--primary-200); /* #bfdbfe - subtle border */
    min-height: 32px; /* Adjust min height if needed */
    min-width: auto; /* Allow smaller buttons */
}

QPushButton.camera-control:hover {
    background: var(--primary-100); /* #dbeafe */
}

QPushButton.camera-control:pressed {
    background: var(--primary-200); /* #bfdbfe */
}

QPushButton.camera-control:disabled {
    background: var(--neutral-100); /* #f3f4f6 */
    color: var(--neutral-400); /* #9ca3af */
    border-color: var(--neutral-200); /* #e5e7eb */
}


/* ----------------------------------------
   Recording Button
----------------------------------------- */
QPushButton.recording { /* Inherits from base QPushButton, overrides color */
    background: var(--error); /* #ef4444 */
    color: white;
    border: none; /* Ensure no border if base button had one */
}

QPushButton.recording:hover {
    background: #dc2626; /* Darker red */
}

QPushButton.recording:pressed {
    background: #b91c1c; /* Even darker red */
}

/* ----------------------------------------
   Camera Control Sliders
----------------------------------------- */
QSlider.camera-slider::groove:horizontal { /* Specific slider style */
    height: 6px;
    background: var(--neutral-200); /* #e5e7eb */
    border-radius: 3px; /* Smaller radius */
}

QSlider.camera-slider::handle:horizontal {
    background: var(--primary-500); /* #3b82f6 */
    width: 16px;
    height: 16px;
    margin: -5px 0; /* Vertically center */
    border-radius: 8px; /* Round */
}

QSlider.camera-slider::sub-page:horizontal {
    background: var(--primary-300); /* #93c5fd */
    border-radius: 3px; /* Smaller radius */
}
"""

# Stylesheet specific to the CT400 control panel
CT400_CONTROL_PANEL_STYLE = """
/* ----------------------------------------
   CT400 Control Panel Base Styling
----------------------------------------- */
/* Apply using setObjectName("ct400ScanPanel") or setObjectName("ct400MonitorPanel") */
QWidget#ct400ScanPanel, QWidget#ct400MonitorPanel {
    background: white;
    border-radius: var(--radius-lg); /* 8px */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    padding: var(--space-lg); /* 16px */
}

/* ----------------------------------------
   Panel Header
----------------------------------------- */
/* ... (existing ct400-title, section-title, etc. styles from your original CT400_CONTROL_PANEL_STYLE) ... */
QLabel.ct400-title {
    font-size: var(--font-lg); /* 18px */
    font-weight: 600;
    color: var(--neutral-800); /* #1f2937 */
    padding: 0 0 var(--space-md) 0; /* 0 0 12px 0 */
    border-bottom: 1px solid var(--neutral-200); /* #e5e7eb */
    qproperty-alignment: AlignLeft;
    margin-bottom: var(--space-md); /* 12px - Add margin below border */
}

QLabel.section-title {
    font-size: 15px; /* Between sm and md */
    font-weight: 600;
    color: var(--neutral-700); /* #374151 */
    padding: var(--space-md) 0 var(--space-sm) 0; /* 12px 0 8px 0 */
    qproperty-alignment: AlignLeft;
}

QFrame.parameters-grid {
    background: var(--neutral-50); /* #f9fafb */
    border-radius: var(--radius-md); /* 6px */
    padding: var(--space-md); /* 12px */
}

QLabel.parameter-name {
    font-weight: 500;
    color: var(--neutral-500); /* #6b7280 */
    qproperty-alignment: AlignLeft;
    padding: 2px 0; /* Minimal padding */
}

QLabel.parameter-value {
    font-weight: 600;
    color: var(--neutral-800); /* #1f2937 */
    qproperty-alignment: AlignRight;
    padding: 2px 0; /* Minimal padding */
}

QLabel.status-label { /* General status label class */
    font-size: 13px; /* Slightly larger than badge */
    font-weight: 500;
    padding: 5px 12px;
    border-radius: var(--radius-full); /* 14px or more */
    qproperty-alignment: AlignCenter;
    min-width: 100px; /* Ensure consistent size */
}

QLabel.status-label[status="operational"] {
    background-color: var(--success-light); /* #d1fae5 */
    color: #065f46; /* Dark green */
}

QLabel.status-label[status="standby"] {
    background-color: var(--warning-light); /* #fef3c7 */
    color: #92400e; /* Dark amber */
}

QLabel.status-label[status="error"] {
    background-color: var(--error-light); /* #fee2e2 */
    color: #b91c1c; /* Dark red */
}

QLabel.status-label[status="calibrating"] {
    background-color: var(--primary-100); /* #dbeafe */
    color: var(--primary-800); /* #1e40af */
}

QPushButton.ct400-primary { /* Specific primary button for CT400 panel */
    background: var(--primary-600); /* #2563eb */
    color: white;
    border: none;
    padding: 10px 20px; /* Larger padding */
    border-radius: var(--radius-md); /* 6px */
    font-weight: 600; /* Bolder */
    min-width: 120px;
}

QPushButton.ct400-primary:hover {
    background: var(--primary-700); /* #1d4ed8 */
}

QPushButton.ct400-primary:pressed {
    background: var(--primary-800); /* #1e40af */
}

QPushButton.ct400-primary:disabled {
    background: var(--neutral-200); /* #e5e7eb */
    color: var(--neutral-400); /* #9ca3af */
}

QPushButton.ct400-secondary { /* Specific secondary button */
    background: var(--neutral-100); /* #f3f4f6 */
    color: var(--neutral-600); /* #4b5563 */
    border: 1px solid var(--neutral-300); /* #d1d5db */
    padding: 10px 20px;
    border-radius: var(--radius-md); /* 6px */
    font-weight: 500;
    min-width: 100px;
}

QPushButton.ct400-secondary:hover {
    background: var(--neutral-200); /* #e5e7eb */
    color: var(--neutral-800); /* #1f2937 */
}

QPushButton.ct400-emergency { /* Specific emergency button */
    background: var(--error); /* #ef4444 */
    color: white;
    border: none;
    padding: 10px 20px;
    border-radius: var(--radius-md); /* 6px */
    font-weight: 600;
    min-width: 120px;
}

QPushButton.ct400-emergency:hover {
    background: #dc2626; /* Darker red */
}

QTextEdit.data-output { /* Specific style for data/log display */
    background: var(--neutral-100); /* #f3f4f6 */
    border: 1px solid var(--neutral-200); /* #e5e7eb */
    border-radius: var(--radius-md); /* 6px */
    padding: var(--space-md); /* 12px */
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 13px; /* Specific font size */
    color: var(--neutral-700); /* #374151 */
}

QLineEdit.ct400-input {}
QLineEdit.ct400-input:focus {}
QComboBox.ct400-selector {}
QComboBox.ct400-selector:focus {}

QProgressBar.ct400-progress {}
QProgressBar.ct400-progress.no-text { color: transparent; }
QProgressBar.ct400-progress::chunk {}

/* ----------------------------------------
   NEW: CT400 Scan and Monitor Buttons (Specific Styling)
----------------------------------------- */
/* Common styles for scan/monitor operation buttons */
QPushButton#scanButtonStart,
QPushButton#scanButtonStop,
QPushButton#monitorButtonStart,
QPushButton#monitorButtonStop {
    font-weight: bold;
    color: white; /* Text color for all these buttons */
    border: none;
    padding: 10px 15px; /* Adjust padding as needed */
    border-radius: var(--radius-md); /* 6px */
    /* min-height is set in Python code, e.g., self.scan_btn.setMinimumHeight(35) */
    /* min-width is set in Python code */
}

/* Scan Button - Start State (Green) */
QPushButton#scanButtonStart {
    background-color: var(--success); /* #10b981 */
}
QPushButton#scanButtonStart:hover {
    background-color: #0f9b6d; /* Darker green */
}
QPushButton#scanButtonStart:pressed {
    background-color: #0d825a; /* Even darker green */
}
QPushButton#scanButtonStart:disabled {
    background: var(--neutral-200);
    color: var(--neutral-400);
}

/* Scan Button - Stop State (Red) */
QPushButton#scanButtonStop {
    background-color: var(--error); /* #ef4444 */
}
QPushButton#scanButtonStop:hover {
    background-color: #e12d2d; /* Darker red */
}
QPushButton#scanButtonStop:pressed {
    background-color: #c51a1a; /* Even darker red */
}
QPushButton#scanButtonStop:disabled { /* Should not typically be disabled in stop state, but for completeness */
    background: var(--neutral-200);
    color: var(--neutral-400);
}

/* Monitor Button - Start State (Blue) */
QPushButton#monitorButtonStart {
    background-color: var(--primary-500); /* #3b82f6 */
}
QPushButton#monitorButtonStart:hover {
    background-color: var(--primary-600); /* #2563eb */
}
QPushButton#monitorButtonStart:pressed {
    background-color: var(--primary-700); /* #1d4ed8 */
}
QPushButton#monitorButtonStart:disabled {
    background: var(--neutral-200);
    color: var(--neutral-400);
}

/* Monitor Button - Stop State (Orange) */
QPushButton#monitorButtonStop {
    background-color: var(--warning); /* #f59e0b */
}
QPushButton#monitorButtonStop:hover {
    background-color: #e08e0a; /* Darker orange */
}
QPushButton#monitorButtonStop:pressed {
    background-color: #c87f09; /* Even darker orange */
}
QPushButton#monitorButtonStop:disabled {
    background: var(--neutral-200);
    color: var(--neutral-400);
}

/* ----------------------------------------
   Status Bar Label for CT400
----------------------------------------- */
QLabel#ct400StatusLabel {
    padding: 3px 10px;
    border-radius: 4px;
    font-weight: 500;
    min-height: 20px;
    margin-right: 5px;
    border: 1px solid transparent;
}

QLabel#ct400StatusLabel[status="unknown"] {
    background-color: var(--neutral-300); color: var(--neutral-700); border-color: var(--neutral-400);
}
QLabel#ct400StatusLabel[status="unavailable"] {
    background-color: var(--neutral-200); color: var(--neutral-500); border-color: var(--neutral-300);
}
QLabel#ct400StatusLabel[status="disconnected"] {
    background-color: var(--neutral-100); color: var(--neutral-600); border-color: var(--neutral-300);
}
QLabel#ct400StatusLabel[status="connecting"],
QLabel#ct400StatusLabel[status="disconnecting"] {
    background-color: var(--warning-light); color: #92400e; border-color: var(--warning);
}
QLabel#ct400StatusLabel[status="connected"] {
    background-color: var(--success-light); color: #065f46; border-color: var(--success);
}
QLabel#ct400StatusLabel[status="error"] {
    background-color: var(--error-light); color: #b91c1c; border-color: var(--error);
}
"""
