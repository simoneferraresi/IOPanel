# IOPanel: Photonics Lab Control GUI

<p align="center">
  <!-- TODO: Replace with an actual screenshot of your application -->
  <img src="https://via.placeholder.com/800x450.png?text=IOPanel+Application+Screenshot" alt="Application Screenshot" width="75%">
</p>

**IOPanel** is a robust and performant desktop application engineered to interface with and control key laboratory equipment for photonics research. Built with Python and PySide6, it provides a centralized, stable, and user-friendly GUI for running wavelength scans, monitoring optical power in real-time, and viewing high-framerate camera feeds for sample alignment.

This application is architected for stability during long-running experiments, with a focus on a responsive user interface, resilient hardware communication, and clear, immediate data visualization.

---

## Key Features

-   **Modular Instrument Control:**
    -   **CT400 Wavelength Scan:** Full control over the Yenista CT400 for configurable wavelength scans. Set start/end wavelengths, resolution, laser power, and speed.
    -   **Real-time Power Monitor:** A multi-channel power monitor displayed as a live-updating histogram, perfect for alignment tasks and stability checks.
-   **High-Performance Multi-Camera Support:**
    -   Simultaneously stream from multiple Vimba-compatible cameras in parallel.
    -   Individual, thread-safe controls for gamma and exposure (including one-shot auto-exposure and auto-gain).
    -   Asynchronous, non-blocking camera initialization.
    -   Automatic connection recovery watchdog for enhanced stability.
-   **Advanced Data Visualization:**
    -   Scan results are plotted instantly using the fast and interactive `pyqtgraph` library.
    -   Live, throttled histogram for smooth power monitoring without overwhelming the CPU.
-   **Robust Data Export:**
    -   Save scan data in multiple formats simultaneously with a single click.
    -   Supported formats: **CSV**, **MATLAB (.mat)**.
    -   **(Optional)** **MATLAB Figure (.fig)** export is available if the MATLAB Engine for Python is installed.
-   **Engineered for Stability:**
    -   **Asynchronous Architecture:** All hardware communication and long-running tasks are executed on background threads, ensuring the GUI remains responsive at all times.
    -   **Type-Safe Configuration:** Powered by **Pydantic**, the application validates the `config.ini` file on startup, preventing errors from invalid settings.
    -   **Resilient Error Handling:** Graceful error handling and built-in hardware watchdogs ensure robust, long-term operation.

---

## Installation

This project is designed to be run from a local Python environment and uses [`uv`](https://github.com/astral-sh/uv) as its recommended package and project manager. `uv` is an extremely fast, all-in-one tool that replaces `pip` and `venv`.

### 1. Prerequisites

-   **Python 3.10+**
-   **`uv`**: Install `uv` on your system.
    -   On macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
    -   On Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
    -   See the [official `uv` installation guide](https://github.com/astral-sh/uv#installation) for more options.
-   **Git**
-   **Required Hardware Drivers:**
    -   **Allied Vision Vimba SDK:** For camera support. Please install the version you have tested with (e.g., Vimba SDK v6.0). Download from the official Allied Vision website.
    -   **Yenista CT400 Drivers:** The `CT400_lib.dll` file is required. This is provided with the instrument. Ensure you have the correct 32-bit or 64-bit version that matches your Python interpreter.
-   **(Optional) MATLAB:** Required *only* for saving scan plots as `.fig` files. If you need this feature, you must also install the MATLAB Engine for Python.

### 2. Environment Setup

The `uv` workflow simplifies environment creation and dependency installation into two main steps.

```bash
# 1. Clone the repository
git clone https://github.com/simoneferraresi/IOPanel.git
cd IOPanel

# 2. Create a virtual environment and install all dependencies
# This single command creates a virtual environment in .venv and installs
# all dependencies from pyproject.toml, including optional test/dev groups.
uv sync --all-extras

# 3. Activate the virtual environment
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On macOS/Linux:
source .venv/bin/activate
```

### 3. (Optional) MATLAB Engine Setup

If you have MATLAB and want `.fig` export functionality, install the MATLAB Engine API for Python into the `uv`-managed environment.

**Make sure your virtual environment is activated first.**

**Example on Windows:**
```powershell
# First, ensure your prompt shows (.venv)
# Then, navigate to the MATLAB installation directory
cd "C:\Program Files\MATLAB\R2023b\extern\engines\python"
# Install the engine into the active environment
python setup.py install
```
[See official MATLAB documentation for details.](https://www.mathworks.com/help/matlab/matlab_external/install-the-matlab-engine-for-python.html)

---

## Configuration

Before running the application for the first time, you must configure your hardware connections in the `config.ini` file.

1.  **Copy the Template:** In the project root, find `config.ini`. If it does not exist, copy `config.template.ini` (if available) or create it from scratch.
2.  **Edit `config.ini`:**
    -   **`[Instruments]` Section:**
        -   Set `ct400_dll_path` to the **absolute path** of your `CT400_lib.dll` file. Use forward slashes (`/`) for compatibility.
        -   Update `tunics_gpib_address` to match your hardware setup.
    -   **`[Camera:*]` Sections:**
        -   For each camera you want to use, create a section like `[Camera:Top]`.
        -   Set `enabled = true`.
        -   Set the `name` to a user-friendly description.
        -   Find the camera's unique `identifier` (e.g., `DEV_...` or a serial number). You can find this using the **`Instruments > Discover Cameras...`** menu item within the application. Copy the ID from the discovery dialog and paste it here.

---

## Usage

Once the environment is set up and `config.ini` is configured, run the application from the project's root directory:

```bash
python app.py
```

### Command-Line Arguments

You can override certain settings from the `config.ini` file using command-line arguments:

-   `--log-level`: Set the logging level (e.g., `DEBUG`, `INFO`).
-   `--log-file`: Specify a different path for the log file.
-   `--config`: Specify a different configuration file path.

**Example:**
```bash
python app.py --log-level DEBUG --config config.production.ini
```

---

## Development

### Compiling Qt Resources

The application uses icons stored in a Qt Resource File (`resources/resources.qrc`). If you add or change icons, you must recompile the `resources_rc.py` file.

Run the following command from the project root:

```bash
pyside6-rcc resources/resources.qrc -o resources/resources_rc.py
```

### Project Structure

The codebase is organized into hardware abstractions, UI components, and a main application entry point.

```
IOPanel/
├── .gitignore
├── .pre-commit-config.yaml
├── app.py                      # Main application entry point, arg parsing, logger setup
├── config.ini                  # User configuration file (local, not in git)
├── config_model.py             # Pydantic models for type-safe configuration
├── LICENSE
├── pyproject.toml              # Project metadata and dependencies (PEP 621)
├── README.md                   # This file
├── hardware/                   # Hardware abstraction layer
│   ├── camera.py               # Vimba camera abstraction class
│   ├── camera_init_worker.py   # Worker for asynchronous camera initialization
│   ├── ct400.py                # Ctypes wrapper for the CT400 DLL
│   ├── ct400_types.py          # Enums and data classes for CT400
│   ├── dummy_ct400.py          # Dummy implementation for testing without hardware
│   └── interfaces.py           # Abstract base classes for hardware
├── resources/                  # Icons and other static assets
│   ├── resources.qrc           # Qt Resource Collection file
│   └── icons/                  # SVG icons for the UI
├── tests/
│   └── test_camera_widgets.py  # Automated tests for UI components
└── ui/                         # All GUI-related code
    ├── camera_widgets.py       # Widgets for camera display and controls
    ├── constants.py            # Centralized UI constants (IDs, messages)
    ├── control_panel.py        # Widgets for instrument control (scan, monitor)
    ├── discovery_dialog.py     # Dialog for finding connected cameras
    ├── main_window.py          # Main QMainWindow, orchestrates all UI components
    ├── plot_widgets.py         # Widgets for plotting (scan graph, histogram)
    └── theme.py                # Global and component-specific QSS stylesheets
```

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.