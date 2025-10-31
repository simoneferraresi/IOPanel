"""
Global and component-specific stylesheets for the Lab Application.

This module centralizes all Qt Style Sheets (QSS) used in the application.
It uses a dynamic generation approach to combine readability and maintainability
while ensuring the final QSS text is 100% identical to the original design specification.

The `_Theme` class contains a single, authoritative `TOKENS` dictionary that serves
as the source of truth for all style values. The QSS constants are defined as
templates that are populated from this dictionary.

This approach provides the organizational benefits of a theme engine while
preserving the exact, character-for-character output of the static stylesheet,
including all conceptual `var()` comments and syntax.

The public API consists of the generated stylesheet strings:
- APP_STYLESHEET: The main, global stylesheet for the entire application.
- CAMERA_PANEL_STYLE: A specific stylesheet for the Camera Panel widgets.
- CT400_CONTROL_PANEL_STYLE: A specific stylesheet for the CT400 Control Panel widgets.
"""


class _Theme:
    """
    Encapsulates the application's design system and generates stylesheets.
    This class is an internal implementation detail.
    """

    def __init__(self):
        # --- DESIGN SYSTEM TOKENS ---
        # A single, authoritative dictionary for all style values. This includes
        # both conceptual `var(...)` strings and raw, hardcoded values.
        self.TOKENS = {
            # --- Conceptual `var(...)` Variables ---
            "c_primary_50": "var(--primary-50)",
            "c_primary_100": "var(--primary-100)",
            "c_primary_200": "var(--primary-200)",
            "c_primary_300": "var(--primary-300)",
            "c_primary_500": "var(--primary-500)",
            "c_primary_600": "var(--primary-600)",
            "c_primary_700": "var(--primary-700)",
            "c_primary_800": "var(--primary-800)",
            "c_neutral_50": "var(--neutral-50)",
            "c_neutral_100": "var(--neutral-100)",
            "c_neutral_200": "var(--neutral-200)",
            "c_neutral_300": "var(--neutral-300)",
            "c_neutral_400": "var(--neutral-400)",
            "c_neutral_500": "var(--neutral-500)",
            "c_neutral_600": "var(--neutral-600)",
            "c_neutral_700": "var(--neutral-700)",
            "c_neutral_800": "var(--neutral-800)",
            "c_success": "var(--success)",
            "c_success_light": "var(--success-light)",
            "c_error": "var(--error)",
            "c_error_light": "var(--error-light)",
            "c_warning": "var(--warning)",
            "c_warning_light": "var(--warning-light)",
            "c_space_xs": "var(--space-xs)",
            "c_space_sm": "var(--space-sm)",
            "c_space_md": "var(--space-md)",
            "c_space_lg": "var(--space-lg)",
            "c_font_xs": "var(--font-xs)",
            "c_font_sm": "var(--font-sm)",
            "c_font_md": "var(--font-md)",
            "c_font_lg": "var(--font-lg)",
            "c_radius_sm": "var(--radius-sm)",
            "c_radius_md": "var(--radius-md)",
            "c_radius_lg": "var(--radius-lg)",
            "c_radius_full": "var(--radius-full)",
            # --- Raw Values and Specific Overrides ---
            "white": "white",
            "transparent": "transparent",
            "none": "none",
            "badge_success_text": "#065f46",
            "badge_warning_text": "#92400e",
            "badge_error_text": "#b91c1c",
            "destructive_text": "#b91c1c",
            "destructive_border": "#fecaca",
            "destructive_hover_bg": "#fecaca",
            "destructive_hover_text": "#991b1b",
            "recording_hover_bg": "#dc2626",
            "recording_pressed_bg": "#b91c1c",
            # --- CT400 Component-Specific Values ---
            "ct400_scan_start_bg": "#007200",
            "ct400_scan_start_hover_bg": "#006400",
            "ct400_scan_start_pressed_bg": "#004b23",
            "ct400_scanning_bg": "#85182a",
            "ct400_scanning_hover_bg": "#6e1423",
            "ct400_scanning_pressed_bg": "#641220",
            "ct400_monitor_start_bg": "#0077b6",
            "ct400_monitor_start_hover_bg": "#023e8a",
            "ct400_monitor_start_pressed_bg": "#03045e",
        }

    def _generate(self, template: str) -> str:
        """Populates a QSS template string with variables from the TOKENS dictionary."""
        return template.format(**self.TOKENS)


# --- QSS TEMPLATES ---
# NOTE: Literal curly braces `{` and `}` in QSS must be escaped as `{{` and `}}`.

_APP_STYLESHEET_TEMPLATE = """
/* ----------------------------------------
   Design System Variables (Conceptual)
-----------------------------------------
   NOTE: QSS does not support CSS variables (`var(...)`).
   These are defined here for clarity and maintainability.
   Use find/replace or consider a QSS pre-processor (like qtsass)
   if extensive theming or easier refactoring is needed.
----------------------------------------- */
:root {{
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
}}

/* ----------------------------------------
   General Widget Styling
----------------------------------------- */
QWidget {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: {c_font_sm}; /* 14px */
    /* Default background for most widgets.
       Consider 'transparent' if issues arise with nested layout widgets
       unexpectedly inheriting a white background. */
    background-color: {white};
    color: {c_neutral_800}; /* #1f2937 */
}}

/* ----------------------------------------
   Label Styling
----------------------------------------- */
QLabel {{
    font-weight: 500;
    color: {c_neutral_700}; /* #374151 */
    padding: {c_space_xs}; /* 4px */
    background: {none}; /* Labels should generally be transparent */
    min-height: 20px; /* Adjust as needed based on typical font size */
}}

QLabel.title {{
    font-size: {c_font_md}; /* 16px */
    font-weight: 600;
    color: {c_neutral_800}; /* #1f2937 */
    padding: {c_space_sm} 0; /* 8px 0 */
}}

QLabel.subtitle {{
    font-size: {c_font_sm}; /* 14px */
    font-weight: 500;
    color: {c_neutral_600}; /* #4b5563 */
}}

QLabel.badge {{
    font-size: {c_font_xs}; /* 12px */
    font-weight: 500;
    padding: 2px 10px; /* Specific padding for badge look */
    border-radius: 12px; /* Closer to var(--radius-full) for pill shape */
    background-color: {c_neutral_200}; /* #e5e7eb */
    color: {c_neutral_700}; /* #374151 */
    qproperty-alignment: AlignCenter;
}}

QLabel.badge-success {{
    background-color: {c_success_light}; /* #d1fae5 */
    color: {badge_success_text}; /* Specific darker shade for contrast */
}}

QLabel.badge-warning {{
    background-color: {c_warning_light}; /* #fef3c7 */
    color: {badge_warning_text}; /* Specific darker shade for contrast */
}}

QLabel.badge-error {{
    background-color: {c_error_light}; /* #fee2e2 */
    color: {badge_error_text}; /* Specific darker shade for contrast */
}}

/* ----------------------------------------
   Group Box Styling
----------------------------------------- */
QGroupBox {{
    font-weight: 600;
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_lg}; /* 8px */
    margin-top: 20px; /* Space for title */
    padding-top: 16px; /* Internal padding below title */
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {c_space_lg}; /* 16px */
    padding: 0 {c_space_sm}; /* 0 8px */
    color: {c_neutral_600}; /* #4b5563 */
    /* background-color: white; */ /* Optional: If needed to mask border */
}}

/* ----------------------------------------
   Input Fields: QLineEdit and QTextEdit
----------------------------------------- */
QLineEdit, QTextEdit {{
    padding: 10px 12px; /* Consider var(--space-sm) var(--space-md) if suitable */
    font-size: {c_font_sm}; /* 14px */
    border: 1px solid {c_neutral_300}; /* #d1d5db */
    border-radius: {c_radius_md}; /* 6px */
    background: {white};
    color: {c_neutral_800}; /* #1f2937 */
    selection-background-color: {c_primary_200}; /* #bfdbfe */
}}

QLineEdit:focus, QTextEdit:focus {{
    border: 2px solid {c_primary_500}; /* #3b82f6 */
    /* Adjust padding slightly to prevent layout shift due to thicker border */
    /* padding: 9px 11px; */ /* Uncomment if needed */
    background-color: {c_neutral_50}; /* #f9fafb */
}}

QLineEdit:hover:!focus, QTextEdit:hover:!focus {{
    border: 1px solid {c_neutral_400}; /* #9ca3af */
}}

QLineEdit:disabled, QTextEdit:disabled {{
    background-color: {c_neutral_100}; /* #f3f4f6 */
    color: {c_neutral_400}; /* #9ca3af */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
}}

QLineEdit[readOnly="true"] {{
    background-color: {c_neutral_100}; /* #f3f4f6 */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
}}

/* ----------------------------------------
   Combo Box Styling
----------------------------------------- */
QComboBox {{
    padding: 10px 36px 10px 12px; /* Right padding accommodates arrow */
    font-size: {c_font_sm}; /* 14px */
    border: 1px solid {c_neutral_300}; /* #d1d5db */
    border-radius: {c_radius_md}; /* 6px */
    background: {white};
    color: {c_neutral_800}; /* #1f2937 */
    min-height: 20px; /* Ensure minimum height */
}}

QComboBox:focus {{
    border: 2px solid {c_primary_500}; /* #3b82f6 */
    /* padding: 9px 35px 9px 11px; */ /* Adjust padding if border shifts layout */
}}

QComboBox:hover:!focus {{
    border: 1px solid {c_neutral_400}; /* #9ca3af */
}}

QComboBox:disabled {{
    background-color: {c_neutral_100}; /* #f3f4f6 */
    color: {c_neutral_400}; /* #9ca3af */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px; /* Width of the dropdown area */
    border-left: {none}; /* Avoid double border */
    border-top-right-radius: {c_radius_md}; /* 6px */
    border-bottom-right-radius: {c_radius_md}; /* 6px */
}}

QComboBox::down-arrow {{
    /* Ensure ':/icons/chevron-down.svg' exists in your compiled .qrc file */
    image: url(:/icons/chevron-down.svg);
    width: 16px;
    height: 16px;
}}

QComboBox QAbstractItemView {{ /* Style for the dropdown list */
    background: {white};
    border: 1px solid {c_neutral_300}; /* #d1d5db */
    border-radius: {c_radius_md}; /* 6px */
    selection-background-color: {c_primary_50}; /* #eff6ff */
    selection-color: {c_primary_800}; /* #1e40af */
    padding: {c_space_xs}; /* 4px */
    outline: {none}; /* Remove focus rectangle around dropdown */
}}

/* ----------------------------------------
   Button Styling
----------------------------------------- */
QPushButton {{
    background: {c_primary_500}; /* #3b82f6 */
    color: {white};
    border: {none};
    padding: 10px {c_space_lg}; /* 10px 16px */
    border-radius: {c_radius_md}; /* 6px */
    font-weight: 500;
    min-height: 38px; /* Consistent clickable height */
    min-width: 80px; /* Ensure some minimum width */
}}

QPushButton:hover {{
    background: {c_primary_600}; /* #2563eb */
}}

QPushButton:pressed {{
    background: {c_primary_700}; /* #1d4ed8 */
}}

QPushButton:disabled {{
    background: {c_neutral_200}; /* #e5e7eb */
    color: {c_neutral_400}; /* #9ca3af */
}}

QPushButton.secondary {{
    background: {c_neutral_100}; /* #f3f4f6 */
    color: {c_neutral_600}; /* #4b5563 */
    border: 1px solid {c_neutral_300}; /* #d1d5db */
}}

QPushButton.secondary:hover {{
    background: {c_neutral_200}; /* #e5e7eb */
    color: {c_neutral_800}; /* #1f2937 */
}}

QPushButton.secondary:pressed {{
    background: {c_neutral_300}; /* #d1d5db */
}}

QPushButton.destructive {{
    background: {c_error_light}; /* #fee2e2 */
    color: {destructive_text}; /* Specific dark red */
    border: 1px solid {destructive_border}; /* Light red border */
}}

QPushButton.destructive:hover {{
    background: {destructive_hover_bg}; /* Lighter red */
    color: {destructive_hover_text}; /* Darker red */
}}

QPushButton.small {{
    padding: 6px {c_space_md}; /* 6px 12px */
    font-size: {c_font_xs}; /* 12px */
    min-height: 28px;
    min-width: 60px;
}}

QPushButton.icon {{
    padding: {c_space_sm}; /* 8px */
    min-width: 38px; /* Square-ish size */
    min-height: 38px;
}}

/* ----------------------------------------
   Checkbox and Radio Button Styling
----------------------------------------- */
QCheckBox, QRadioButton {{
    spacing: {c_space_sm}; /* 8px */
    color: {c_neutral_700}; /* #374151 */
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {c_neutral_400}; /* #9ca3af */
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px;
    height: 18px;
}}

QCheckBox::indicator:unchecked {{
    border: 2px solid {c_neutral_300}; /* #d1d5db */
    border-radius: {c_radius_sm}; /* 4px */
    background-color: {white};
}}

QCheckBox::indicator:unchecked:hover {{
    border: 2px solid {c_neutral_400}; /* #9ca3af */
}}

QCheckBox::indicator:checked {{
    border: 2px solid {c_primary_500}; /* #3b82f6 */
    border-radius: {c_radius_sm}; /* 4px */
    background-color: {c_primary_500}; /* #3b82f6 */
    /* Ensure ':/icons/check.svg' exists in your compiled .qrc file and is suitable */
    image: url(:/icons/check.svg);
}}

QCheckBox::indicator:checked:disabled {{
    border: 2px solid {c_neutral_300}; /* #d1d5db */
    background-color: {c_neutral_300}; /* #d1d5db */
    /* Add disabled check icon if needed */
}}

QRadioButton::indicator:unchecked {{
    border: 2px solid {c_neutral_300}; /* #d1d5db */
    border-radius: 10px; /* Round */
    background-color: {white};
}}

QRadioButton::indicator:unchecked:hover {{
    border: 2px solid {c_neutral_400}; /* #9ca3af */
}}

QRadioButton::indicator:checked {{
    border: 2px solid {c_primary_500}; /* #3b82f6 */
    border-radius: 10px; /* Round */
    background-color: {white}; /* Background for the inner dot */
    /* Ensure ':/icons/radio-checked.svg' exists in your compiled .qrc file */
    image: url(:/icons/radio-checked.svg); /* This should be the inner dot image */
}}

QRadioButton::indicator:checked:disabled {{
    border: 2px solid {c_neutral_300}; /* #d1d5db */
    background-color: {c_neutral_100}; /* #f3f4f6 */
    /* Add disabled radio dot icon if needed */
    image: {none}; /* Or a specific disabled dot */
}}


/* ----------------------------------------
   Slider Styling
----------------------------------------- */
QSlider::groove:horizontal {{
    border: {none};
    height: 8px;
    background: {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_sm}; /* 4px */
}}

QSlider::handle:horizontal {{
    background: {c_primary_500}; /* #3b82f6 */
    border: {none};
    width: 18px;
    height: 18px;
    margin: -5px 0; /* Vertically center handle on groove */
    border-radius: 9px; /* Round handle */
}}

QSlider::handle:horizontal:hover {{
    background: {c_primary_600}; /* #2563eb */
}}

QSlider::sub-page:horizontal {{ /* Style for the part before the handle */
    background: {c_primary_300}; /* #93c5fd */
    border-radius: {c_radius_sm}; /* 4px */
}}

/* ----------------------------------------
   Progress Bar Styling
----------------------------------------- */
QProgressBar {{
    border: {none};
    background: {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_sm}; /* 4px */
    text-align: center;
    color: {c_neutral_700}; /* #374151 - Default text color, visible */
    height: 8px;
}}

/* Optional class to hide the percentage text if needed */
QProgressBar.no-text {{
    color: {transparent};
}}

QProgressBar::chunk {{
    background-color: {c_primary_500}; /* #3b82f6 */
    border-radius: {c_radius_sm}; /* 4px */
}}

/* ----------------------------------------
   Tab Widget Styling
----------------------------------------- */
QTabWidget::pane {{ /* The area where tab content is shown */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_lg}; /* 8px */
    /* Shift pane down slightly to connect visually with selected tab */
    top: -1px;
    background: {white}; /* Ensure pane background is white */
}}

QTabBar::tab {{
    background: {c_neutral_50}; /* #f9fafb */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-bottom: {none}; /* Connects to pane border */
    border-top-left-radius: {c_radius_md}; /* 6px */
    border-top-right-radius: {c_radius_md}; /* 6px */
    padding: {c_space_sm} {c_space_lg}; /* 8px 16px */
    margin-right: {c_space_xs}; /* 4px */
    color: {c_neutral_500}; /* #6b7280 */
}}

QTabBar::tab:selected {{
    background: {white}; /* Match pane background */
    color: {c_neutral_800}; /* #1f2937 */
    font-weight: 500;
    /* border-bottom: 1px solid white; */ /* Hide bottom border by matching background */
}}

QTabBar::tab:!selected {{
    margin-top: 2px; /* Slightly lower unselected tabs */
}}

QTabBar::tab:hover:!selected {{
    background: {c_neutral_100}; /* #f3f4f6 */
    color: {c_neutral_700}; /* #374151 */
}}

/* ----------------------------------------
   Scroll Area Styling
----------------------------------------- */
QScrollArea {{
    background: {transparent}; /* Usually want the scroll area itself transparent */
    border: {none};
}}

/* Ensure the widget *inside* the QScrollArea has a background if needed */
QScrollArea > QWidget > QWidget {{
     background: {white}; /* Or transparent depending on content */
}}


/* ----------------------------------------
   Scroll Bars Styling
----------------------------------------- */
QScrollBar:vertical {{
    background: {c_neutral_50}; /* #f9fafb */
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {c_neutral_300}; /* #d1d5db */
    min-height: 30px;
    border-radius: 6px; /* Rounded handle */
}}

QScrollBar::handle:vertical:hover {{
    background: {c_neutral_400}; /* #9ca3af */
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    /* Hide the top/bottom arrows */
    height: 0px;
    border: {none};
    background: {none};
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    /* Background of the track */
    background: {none};
}}

QScrollBar:horizontal {{
    background: {c_neutral_50}; /* #f9fafb */
    height: 12px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {c_neutral_300}; /* #d1d5db */
    min-width: 30px;
    border-radius: 6px; /* Rounded handle */
}}

QScrollBar::handle:horizontal:hover {{
    background: {c_neutral_400}; /* #9ca3af */
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    /* Hide the left/right arrows */
    width: 0px;
    border: {none};
    background: {none};
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    /* Background of the track */
    background: {none};
}}


/* ----------------------------------------
   Status Bar Styling
----------------------------------------- */
QStatusBar {{
    background: {c_neutral_50}; /* #f9fafb */
    border-top: 1px solid {c_neutral_200}; /* #e5e7eb */
    color: {c_neutral_500}; /* #6b7280 */
}}

QStatusBar::item {{
    border: {none}; /* No borders around status bar sections */
}}

/* ----------------------------------------
   Menu Styling
----------------------------------------- */
QMenuBar {{
    background-color: {c_neutral_50}; /* #f9fafb */
    border-bottom: 1px solid {c_neutral_200}; /* #e5e7eb */
}}

QMenuBar::item {{
    padding: {c_space_sm} {c_space_md}; /* 8px 12px */
    background: {transparent};
}}

QMenuBar::item:selected {{ /* Hover state */
    background: {c_primary_50}; /* #eff6ff */
    border-radius: {c_radius_sm}; /* 4px */
}}

QMenuBar::item:pressed {{ /* When menu is open */
    background: {c_primary_100}; /* #dbeafe */
}}

QMenu {{
    background-color: {white};
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_md}; /* 6px */
    padding: {c_space_sm} 0; /* 8px 0 */
}}

QMenu::item {{
    padding: 8px 32px 8px 16px; /* Space for checkmark/icon and text */
    color: {c_neutral_700}; /* #374151 */
}}

QMenu::item:selected {{ /* Hover/active state */
    background: {c_primary_50}; /* #eff6ff */
    color: {c_primary_800}; /* #1e40af */
}}

QMenu::separator {{
    height: 1px;
    background: {c_neutral_200}; /* #e5e7eb */
    margin: {c_space_xs} 0; /* 4px 0 */
}}

/* ----------------------------------------
   Tooltip Styling
----------------------------------------- */
QToolTip {{
    background-color: {c_neutral_800}; /* #1f2937 */
    color: {white};
    border: {none};
    border-radius: {c_radius_sm}; /* 4px */
    padding: 6px 10px;
    opacity: 220; /* Qt specific opacity */
}}

/* ----------------------------------------
   Frame Styling (Generic & Custom)
----------------------------------------- */
QFrame {{
    /* Default frame has no border or background */
    border: {none};
    background: {transparent};
}}

QFrame.card {{
    background: {white};
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_lg}; /* 8px */
    padding: {c_space_lg}; /* 16px */
}}

QFrame.separator {{
    background: {c_neutral_200}; /* #e5e7eb */
}}

/* Need to use property selector for orientation */
QFrame.separator[orientation="1"] {{ /* QFrame.HLine == 1 */
    height: 1px;
    min-height: 1px;
    max-height: 1px;
}}

QFrame.separator[orientation="2"] {{ /* QFrame.VLine == 2 */
    width: 1px;
    min-width: 1px;
    max-width: 1px;
}}
"""

_CAMERA_PANEL_STYLE_TEMPLATE = """
/* ----------------------------------------
   Camera Panel Container
----------------------------------------- */
/* Apply using setObjectName("camera-panel") or a dynamic property */
QFrame#camera-panel, QFrame[panelType="camera"] {{
    background: {white};
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_lg}; /* 8px */
    padding: {c_space_lg}; /* 16px */
}}

/* ----------------------------------------
   Camera Display Area
----------------------------------------- */
QLabel.camera-view {{
    background-color: {c_neutral_100}; /* #f3f4f6 */
    border-radius: {c_radius_lg}; /* 8px */
    min-height: 240px; /* Example minimum height */
    qproperty-alignment: AlignCenter;
    color: {c_neutral_400}; /* #9ca3af - For placeholder text like 'No Signal' */
}}

/* ----------------------------------------
   Title Label in Camera Panel
----------------------------------------- */
QLabel.title-label {{ /* Use this class for titles within the panel */
    font-size: {c_font_md}; /* 16px */
    font-weight: 600;
    color: {c_neutral_800}; /* #1f2937 */
    margin-bottom: {c_space_sm}; /* 8px */
    padding: 0; /* Override default QLabel padding if needed */
}}

/* ----------------------------------------
   Status Indicator in Camera Panel
----------------------------------------- */
QLabel.camera-status {{ /* Use this class for status text */
    font-size: {c_font_xs}; /* 12px */
    padding: 4px 12px; /* Specific padding */
    border-radius: {c_radius_full}; /* 12px or more for pill shape */
    qproperty-alignment: AlignCenter;
    font-weight: 500;
}}

/* Use dynamic properties to set the status */
QLabel.camera-status[status="active"] {{
    background-color: {c_success_light}; /* #d1fae5 */
    color: {badge_success_text}; /* Dark green */
}}

QLabel.camera-status[status="standby"] {{
    background-color: {c_warning_light}; /* #fef3c7 */
    color: {badge_warning_text}; /* Dark amber */
}}

QLabel.camera-status[status="offline"] {{
    background-color: {c_error_light}; /* #fee2e2 */
    color: {badge_error_text}; /* Dark red */
}}

/* ----------------------------------------
   Buttons in Camera Panel
----------------------------------------- */
QPushButton.camera-control {{ /* Specific button style for this panel */
    background: {c_primary_50}; /* #eff6ff */
    color: {c_primary_800}; /* #1e40af */
    padding: 8px 12px; /* Slightly smaller padding */
    border-radius: {c_radius_md}; /* 6px */
    font-weight: 500;
    border: 1px solid {c_primary_200}; /* #bfdbfe - subtle border */
    min-height: 32px; /* Adjust min height if needed */
    min-width: auto; /* Allow smaller buttons */
}}

QPushButton.camera-control:hover {{
    background: {c_primary_100}; /* #dbeafe */
}}

QPushButton.camera-control:pressed {{
    background: {c_primary_200}; /* #bfdbfe */
}}

QPushButton.camera-control:disabled {{
    background: {c_neutral_100}; /* #f3f4f6 */
    color: {c_neutral_400}; /* #9ca3af */
    border-color: {c_neutral_200}; /* #e5e7eb */
}}


/* ----------------------------------------
   Recording Button
----------------------------------------- */
QPushButton.recording {{ /* Inherits from base QPushButton, overrides color */
    background: {c_error}; /* #ef4444 */
    color: {white};
    border: {none}; /* Ensure no border if base button had one */
}}

QPushButton.recording:hover {{
    background: {recording_hover_bg}; /* Darker red */
}}

QPushButton.recording:pressed {{
    background: {recording_pressed_bg}; /* Even darker red */
}}

/* ----------------------------------------
   Camera Control Sliders
----------------------------------------- */
QSlider.camera-slider::groove:horizontal {{ /* Specific slider style */
    height: 6px;
    background: {c_neutral_200}; /* #e5e7eb */
    border-radius: 3px; /* Smaller radius */
}}

QSlider.camera-slider::handle:horizontal {{
    background: {c_primary_500}; /* #3b82f6 */
    width: 16px;
    height: 16px;
    margin: -5px 0; /* Vertically center */
    border-radius: 8px; /* Round */
}}

QSlider.camera-slider::sub-page:horizontal {{
    background: {c_primary_300}; /* #93c5fd */
    border-radius: 3px; /* Smaller radius */
}}
"""

_CT400_CONTROL_PANEL_STYLE_TEMPLATE = """
/* ----------------------------------------
   CT400 Control Panel Base Styling
----------------------------------------- */
/* Apply using setObjectName("ct400ScanPanel") or setObjectName("ct400MonitorPanel") */
QWidget#ct400ScanPanel, QWidget#ct400MonitorPanel {{
    background: {white};
    border-radius: {c_radius_lg}; /* 8px */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    padding: {c_space_lg}; /* 16px */
}}

/* ----------------------------------------
   Panel Header
----------------------------------------- */
QLabel.ct400-title {{
    font-size: {c_font_lg}; /* 18px */
    font-weight: 600;
    color: {c_neutral_800}; /* #1f2937 */
    padding: 0 0 {c_space_md} 0; /* 0 0 12px 0 */
    border-bottom: 1px solid {c_neutral_200}; /* #e5e7eb */
    qproperty-alignment: AlignLeft;
    margin-bottom: {c_space_md}; /* 12px - Add margin below border */
}}

QLabel.section-title {{
    font-size: 15px; /* Between sm and md */
    font-weight: 600;
    color: {c_neutral_700}; /* #374151 */
    padding: {c_space_md} 0 {c_space_sm} 0; /* 12px 0 8px 0 */
    qproperty-alignment: AlignLeft;
}}

QFrame.parameters-grid {{
    background: {c_neutral_50}; /* #f9fafb */
    border-radius: {c_radius_md}; /* 6px */
    padding: {c_space_md}; /* 12px */
}}

QLabel.parameter-name {{
    font-weight: 500;
    color: {c_neutral_500}; /* #6b7280 */
    qproperty-alignment: AlignLeft;
    padding: 2px 0; /* Minimal padding */
}}

QLabel.parameter-value {{
    font-weight: 600;
    color: {c_neutral_800}; /* #1f2937 */
    qproperty-alignment: AlignRight;
    padding: 2px 0; /* Minimal padding */
}}

QLabel.status-label {{ /* General status label class */
    font-size: 13px; /* Slightly larger than badge */
    font-weight: 500;
    padding: 5px 12px;
    border-radius: {c_radius_full}; /* 14px or more */
    qproperty-alignment: AlignCenter;
    min-width: 100px; /* Ensure consistent size */
}}

QLabel.status-label[status="operational"] {{
    background-color: {c_success_light}; /* #d1fae5 */
    color: {badge_success_text}; /* Dark green */
}}

QLabel.status-label[status="standby"] {{
    background-color: {c_warning_light}; /* #fef3c7 */
    color: {badge_warning_text}; /* Dark amber */
}}

QLabel.status-label[status="error"] {{
    background-color: {c_error_light}; /* #fee2e2 */
    color: {badge_error_text}; /* Dark red */
}}

QLabel.status-label[status="calibrating"] {{
    background-color: {c_primary_100}; /* #dbeafe */
    color: {c_primary_800}; /* #1e40af */
}}

QPushButton.ct400-primary {{ /* Specific primary button for CT400 panel */
    background: {c_primary_600}; /* #2563eb */
    color: {white};
    border: {none};
    padding: 10px 20px; /* Larger padding */
    border-radius: {c_radius_md}; /* 6px */
    font-weight: 600; /* Bolder */
    min-width: 120px;
}}

QPushButton.ct400-primary:hover {{
    background: {c_primary_700}; /* #1d4ed8 */
}}

QPushButton.ct400-primary:pressed {{
    background: {c_primary_800}; /* #1e40af */
}}

QPushButton.ct400-primary:disabled {{
    background: {c_neutral_200}; /* #e5e7eb */
    color: {c_neutral_400}; /* #9ca3af */
}}

QPushButton.ct400-secondary {{ /* Specific secondary button */
    background: {c_neutral_100}; /* #f3f4f6 */
    color: {c_neutral_600}; /* #4b5563 */
    border: 1px solid {c_neutral_300}; /* #d1d5db */
    padding: 10px 20px;
    border-radius: {c_radius_md}; /* 6px */
    font-weight: 500;
    min-width: 100px;
}}

QPushButton.ct400-secondary:hover {{
    background: {c_neutral_200}; /* #e5e7eb */
    color: {c_neutral_800}; /* #1f2937 */
}}

QPushButton.ct400-emergency {{ /* Specific emergency button */
    background: {c_error}; /* #ef4444 */
    color: {white};
    border: {none};
    padding: 10px 20px;
    border-radius: {c_radius_md}; /* 6px */
    font-weight: 600;
    min-width: 120px;
}}

QPushButton.ct400-emergency:hover {{
    background: {recording_hover_bg}; /* Darker red */
}}

QTextEdit.data-output {{ /* Specific style for data/log display */
    background: {c_neutral_100}; /* #f3f4f6 */
    border: 1px solid {c_neutral_200}; /* #e5e7eb */
    border-radius: {c_radius_md}; /* 6px */
    padding: {c_space_md}; /* 12px */
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 13px; /* Specific font size */
    color: {c_neutral_700}; /* #374151 */
}}

/* ----------------------------------------
   CT400 Scan and Monitor Buttons (Specific Styling with Dynamic Properties)
----------------------------------------- */

/* Scan Button - Default State (Ready to Start Scan) */
QPushButton#scanButton {{
    background-color: {ct400_scan_start_bg}; /* Green */
    color: {white};
    font-weight: bold;
    border: {none};
    padding: 8px 12px;
    border-radius: 3px; /* var(--radius-md) */
}}
QPushButton#scanButton:hover {{
    background-color: {ct400_scan_start_hover_bg}; /* Darker green */
}}
QPushButton#scanButton:pressed {{
    background-color: {ct400_scan_start_pressed_bg}; /* Even darker green */
}}
QPushButton#scanButton:disabled {{
    background-color: {c_neutral_200}; /* #e0e0e0 */
    color: {c_neutral_400}; /* #bdbdbd */
}}
/* Scan Button - Scanning State (Ready to Stop Scan) */
QPushButton#scanButton[scanning="true"] {{
    background-color: {ct400_scanning_bg}; /* Red */
}}
QPushButton#scanButton[scanning="true"]:hover {{
    background-color: {ct400_scanning_hover_bg}; /* Darker red */
}}
QPushButton#scanButton[scanning="true"]:pressed {{
    background-color: {ct400_scanning_pressed_bg}; /* Even darker red */
}}

/* Monitor Button - Default State (Ready to Start Monitoring) */
QPushButton#monitorButton {{
    background-color: {ct400_monitor_start_bg}; /* Blue */
    color: {white};
    font-weight: bold;
    border: {none};
    padding: 10px 15px;
    border-radius: 3px; /* var(--radius-md) */
}}
QPushButton#monitorButton:hover {{
    background-color: {ct400_monitor_start_hover_bg}; /* Darker blue */
}}
QPushButton#monitorButton:pressed {{
    background-color: {ct400_monitor_start_pressed_bg}; /* Even darker blue */
}}
QPushButton#monitorButton:disabled {{
    background-color: {c_neutral_200}; /* #e0e0e0 */
    color: {c_neutral_400}; /* #bdbdbd */
}}
/* Monitor Button - Monitoring State (Ready to Stop Monitoring) */
QPushButton#monitorButton[monitoring="true"] {{
    background-color: {ct400_scanning_bg}; /* Red */
    color: {white};
}}
QPushButton#monitorButton[monitoring="true"]:hover {{
    background-color: {ct400_scanning_hover_bg}; /* Darker Red */
}}
QPushButton#monitorButton[monitoring="true"]:pressed {{
    background-color: {ct400_scanning_pressed_bg}; /* Even Darker Red */
}}

/* ----------------------------------------
   Alignment and Mapping Buttons
----------------------------------------- */
/* Default State (Green) */
QPushButton#alignButton, QPushButton#mapButton {{
    background-color: {ct400_scan_start_bg}; /* Re-use the same green color */
    color: {white};
    font-weight: bold;
    border: none;
}}
QPushButton#alignButton:hover, QPushButton#mapButton:hover {{
    background-color: {ct400_scan_start_hover_bg};
}}
QPushButton#alignButton:pressed, QPushButton#mapButton:pressed {{
    background-color: {ct400_scan_start_pressed_bg};
}}
QPushButton#alignButton:disabled, QPushButton#mapButton:disabled {{
    background-color: {c_neutral_200};
    color: {c_neutral_400};
}}

/* Running State (Red) */
QPushButton#alignButton[running="true"],
QPushButton#mapButton[running="true"] {{
    background-color: {ct400_scanning_bg}; /* Re-use the same red color */
}}
QPushButton#alignButton[running="true"]:hover,
QPushButton#mapButton[running="true"]:hover {{
    background-color: {ct400_scanning_hover_bg};
}}
QPushButton#alignButton[running="true"]:pressed,
QPushButton#mapButton[running="true"]:pressed {{
    background-color: {ct400_scanning_pressed_bg};
}}

/* ----------------------------------------
   Status Bar Label for CT400
----------------------------------------- */
QLabel#ct400StatusLabel {{
    padding: 3px 10px;
    border-radius: 4px;
    font-weight: 500;
    min-height: 20px;
    margin-right: 5px;
    border: 1px solid {transparent};
}}
QLabel#ct400StatusLabel[status="unknown"] {{
    background-color: {c_neutral_300}; color: {c_neutral_700}; border-color: {c_neutral_400};
}}
QLabel#ct400StatusLabel[status="unavailable"] {{
    background-color: {c_neutral_200}; color: {c_neutral_500}; border-color: {c_neutral_300};
}}
QLabel#ct400StatusLabel[status="disconnected"] {{
    background-color: {c_neutral_100}; color: {c_neutral_600}; border-color: {c_neutral_300};
}}
QLabel#ct400StatusLabel[status="connecting"],
QLabel#ct400StatusLabel[status="disconnecting"] {{
    background-color: {c_warning_light}; color: {badge_warning_text}; border-color: {c_warning};
}}
QLabel#ct400StatusLabel[status="connected"] {{
    background-color: {c_success_light}; color: {badge_success_text}; border-color: {c_success};
}}
QLabel#ct400StatusLabel[status="error"] {{
    background-color: {c_error_light}; color: {badge_error_text}; border-color: {c_error};
}}
"""

# --- PUBLIC API ---
# Create a single theme instance and generate the stylesheets for export.
_theme = _Theme()

APP_STYLESHEET = _theme._generate(_APP_STYLESHEET_TEMPLATE)
CAMERA_PANEL_STYLE = _theme._generate(_CAMERA_PANEL_STYLE_TEMPLATE)
CT400_CONTROL_PANEL_STYLE = _theme._generate(_CT400_CONTROL_PANEL_STYLE_TEMPLATE)
