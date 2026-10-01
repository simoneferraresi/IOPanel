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

-   **Python 3.12+**
-   **`uv`**: Install `uv` on your system.
    -   On macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
    -   On Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`
    -   See the [official `uv` installation guide](https://github.com/astral-sh/uv#installation) for more options.
-   **Git**
-   **Required Hardware Drivers:**
    -   **Allied Vision Vimba X:** For physical camera support. The preferred laboratory profile is Vimba X 2026-2 with VmbPy 1.2.2, VmbC 1.3.1 and VmbImageTransform 2.3 on Windows x64 / Python 3.12.8. See the [VmbPy compatibility profiles](docs/vmbpy-compatibility.md).
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

### Local development workflow

The hardware-independent install does not require the optional Vimba driver dependency. Use a project-local environment and the committed lock file:

Use a project-local environment; do not install project packages into the system Python. After cloning or switching branches, create/update the environment with:

```bash
uv sync --extra test
uv run pytest -q
```

The test extra installs pytest and pytest-qt. Run a focused test while iterating, for example `uv run pytest tests/test_config_model.py -q` or `uv run pytest tests/test_mainwindow_camera_integration.py -q`. The full suite exercises Qt widgets and currently runs on the standard Windows GitHub Actions runner without proprietary drivers.

The full hardware-independent suite runs without proprietary instrument drivers. CI runs this suite for pull requests targeting `main`.

Development and CI should not require proprietary hardware drivers. `hardware/dummy_ct400.py` provides the existing `DummyCT400` implementation; use it for CT400-independent work and tests. Camera discovery and opening should be exercised with mocks or the driver's unavailable path. Do not treat these tests as evidence that a physical CT400 or camera works.

Camera support has driver-free development, the preferred physical-camera profile, and a historical rollback profile:

- **Driver-free development:** VmbPy is not required. Use the simulated camera backend for camera UI work; application and simulation imports remain available when VmbPy or its runtime is unavailable.
- **Preferred laboratory camera profile:** Windows x64, Python 3.12.8, Allied Vision Vimba X 2026-2, VmbPy 1.2.2, VmbC 1.3.1, and VmbImageTransform 2.3, using the Vimba/Vimba X physical backend. This isolated project/candidate environment has been physically validated with two Allied Vision Mako G-125B cameras. See the [qualification record](docs/laboratory-validation-report.md).
- **Historical rollback profile:** Windows x64, Python 3.12.8, Vimba X 2024.1.0.3916, VmbC 1.0.6, and VmbPy 1.0.5 was previously validated and is retained for rollback/reference.

The completed camera qualification includes both standalone streams; normal IOPanel startup with both cameras and DummyCT400; converted-frame display; clean shutdown and VmbSystem release; manual Exposure and Gamma on both cameras; one-shot Auto Exposure on both; and one-shot Auto Gain on both after the Vimba X update. Continuous dual-camera use succeeded during normal operation, but no timed endurance gate was run. CT400 was intentionally simulated, so physical CT400 operation remains unvalidated and requires separate validation. The VmbPy API is optional for CT400-only and simulated-camera work.

For driver-free camera UI development, set `backend = simulation` in an enabled `[Camera:*]` section. The simulator delivers a deterministic mono8 quadrant pattern through the normal camera panel pipeline; its displayed name is marked `[SIMULATED]`. `simulation_width` and `simulation_height` configure frame dimensions. The default backend remains `vimba`, and a missing or failed physical camera is never replaced by the simulator. For operator-supervised CT400 and Allied Vision checks, follow the [laboratory hardware validation protocol](docs/laboratory-hardware-validation.md); no driver-free result establishes physical compatibility.

Perform hardware integration checks on the laboratory PC, where the CT400 DLL, its matching architecture/runtime, the Allied Vision SDK, and physical devices are available. Check startup, CT400 connection and scan behavior, camera discovery, opening/streaming, and clean shutdown there. Keep these checks separate from the driver-free automated test job.

For changes, branch from the intended base (`main` for independent work), link the branch to a GitHub issue, and keep each PR scoped to one issue. Use descriptive commit subjects in imperative form, run the focused tests and `git diff --check`, then open a draft PR when review is useful. Add tests for behavior changes and state clearly which hardware checks remain outstanding. CI should run hardware-independent tests on a standard runner with no proprietary drivers installed; hardware integration results must be recorded separately after lab testing.

### Compiling Qt Resources

The SVG source assets under `resources/icons/` and their aliases in
`resources/resources.qrc` are versioned. `resources/resources_rc.py` is generated
from that source and intentionally committed so a fresh clone can launch with
`python app.py` without a separate resource-compilation step. When changing an
icon or the resource collection, regenerate the module from the project
environment and commit it with the source changes:

Run the following command from the project root:

```bash
uv run pyside6-rcc resources/resources.qrc -o resources/resources_rc.py
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
```

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
