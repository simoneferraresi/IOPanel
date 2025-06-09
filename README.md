# Photonics Lab Control GUI

  <!-- TODO: Replace with an actual screenshot of your application -->

**Photonics Lab Control** is a robust and performant desktop application designed to interface with and control key laboratory equipment for photonics research. Built with Python and PySide6, it provides a centralized and user-friendly GUI for running wavelength scans, monitoring optical power in real-time, and viewing camera feeds for sample alignment.

This application is engineered for stability during long-running experiments, with a focus on a responsive user interface, resilient hardware communication, and clear data visualization.

---

## Features

-   **Modular Instrument Control:**
    -   **CT400 Wavelength Scan:** Full control over the Yenista CT400 for configurable wavelength scans. Set start/end wavelengths, resolution, laser power, and speed.
    -   **Real-time Power Monitor:** A multi-channel power monitor displayed as a live-updating histogram, perfect for alignment and stability checks.
-   **Multi-Camera Support:**
    -   Simultaneously stream from multiple Vimba-compatible cameras.
    -   Individual controls for gamma and exposure (including one-shot auto-exposure).
    -   High-performance, low-latency video display.
    -   Automatic connection recovery if a camera is disconnected.
-   **High-Performance Data Visualization:**
    -   Scan results plotted instantly using the fast `pyqtgraph` library.
    -   Live histogram for power monitoring.
-   **Robust Data Export:**
    -   Save scan data in multiple formats simultaneously with a single click.
    -   Supported formats: **CSV**, **MATLAB (.mat)**, **PNG**, **SVG**.
    -   **MATLAB Figure (.fig)** export available if MATLAB Engine for Python is installed.
-   **Resilient and Stable:**
    -   Asynchronous task handling ensures the GUI never freezes during scans or hardware communication.
    -   Built-in watchdogs and error handling for robust, long-term operation.
-   **Highly Configurable:**
    -   All instrument addresses, camera identifiers, and default scan parameters are managed in a simple `config.ini` file. No hard-coded values.
    -   Flexible logging configuration for easy debugging.

---

## Installation

This application is designed to be run from a Python environment. The following steps will guide you through the setup process.

### 1. Prerequisites

-   **Python 3.10+**
-   **Required Hardware Drivers:**
    -   **Allied Vision Vimba SDK:** For camera support. Please install the specific version you have tested with (e.g., Vimba SDK v6.0). Download from the official Allied Vision website.
    -   **Yenista CT400 Drivers:** The `CT400_lib.dll` file is required. This is typically provided with the instrument. Ensure you have the correct 32-bit or 64-bit version that matches your Python interpreter.
-   **(Optional) MATLAB:** Required only for saving scan plots as `.fig` files. You must also install the MATLAB Engine for Python.

### 2. Setting up the Environment

This project uses modern Python packaging and recommends [`uv`](https://github.com/astral-sh/uv), a fast Python package manager.

```bash
# 1. Clone the repository
git clone https://your-repo-url.git
cd photonics-lab-control

# 2. Create and activate a virtual environment
# This command creates a virtual environment named .venv in the current directory
uv venv
source .venv/bin/activate  # On macOS/Linux
# .venv\Scripts\Activate.ps1  # On Windows (PowerShell)

# 3. Install the application and its dependencies
# uv will read pyproject.toml and install everything in editable mode.
uv pip install -e .
```

### 3. (Optional) MATLAB Engine Setup

If you have MATLAB and want `.fig` export functionality, install the MATLAB Engine API for Python. Navigate to the appropriate MATLAB directory in your terminal and run the installation command.

**On Windows (example path):**
```powershell
cd "C:\Program Files\MATLAB\R2023b\extern\engines\python"
python setup.py install
```
[Official MATLAB Documentation for installation](https://www.mathworks.com/help/matlab/matlab_external/install-the-matlab-engine-for-python.html)

---

## Configuration

Before running the application for the first time, you must create and configure the `config.ini` file.

1.  **Create `config.ini`:** A file named `config.template.ini` should be present in the repository. Make a copy of this file and rename it to `config.ini`. The `config.ini` file is ignored by Git, so your local settings will not be committed.
2.  **Edit `config.ini`:**
    -   **`[Instruments]`**: Set `ct400_dll_path` to the **absolute path** of your `CT400_lib.dll` file. Update the `tunics_gpib_address`.
    -   **`[Camera:*]`**: For each camera you want to use:
        -   Set `enabled = true`.
        -   Find the camera's unique `identifier` using a Vimba utility (the application will also log available camera IDs on startup if it can't find a configured one).
        -   Give it a descriptive `name`.

---

## Usage

Once the environment is set up and `config.ini` is configured, run the application from the project's root directory:

```bash
python app.py
```

### Command-line Arguments

You can override logging and configuration settings from the command line:

-   `--log-level`: Set the logging level (e.g., `DEBUG`, `INFO`).
-   `--log-file`: Specify a different path for the log file.
-   `--config`: Specify a different configuration file path.

Example:
```bash
python app.py --log-level DEBUG --config config.production.ini
```

---

## Project Structure

```
simoneferraresi-iopanel/
├── app.py                  # Main application entry point, argument parsing
├── build.py                # Build script for compiling Qt resources
├── config.ini              # User configuration file (ignored by git)
├── config.template.ini     # A template for users to copy
├── config_model.py         # Type-safe dataclass model for configuration
├── pyproject.toml          # Project metadata and dependencies (PEP 621)
├── README.md               # This file
├── LICENSE                 # Project license
├── hardware/               # Hardware abstraction layer
│   ├── __init__.py
│   ├── camera.py           # Vimba camera abstraction class
│   └── ct400.py            # Ctypes wrapper for the CT400 DLL
├── resources/              # Icons and other static assets
│   ├── resources.qrc       # Qt Resource Collection file
│   ├── resources_rc.py     # Compiled Python version of resources
│   └── icons/              # SVG icons
└── ui/                     # All GUI-related code
    ├── __init__.py
    ├── camera_widgets.py   # Widgets for camera display and controls
    ├── control_panel.py    # Widgets for instrument control (scan, monitor)
    ├── main_window.py      # Main QMainWindow, orchestrates all UI components
    ├── plot_widgets.py     # Widgets for plotting (scan graph, histogram)
    └── theme.py            # Global and component-specific QSS stylesheets
```

### Compiling Qt Resources

The application uses icons stored in a Qt Resource File (`resources/resources.qrc`). If you add or change icons, you must recompile the `resources_rc.py` file using the build script:

```bash
python build.py
```
This script is smart and will only rebuild if it detects changes, saving time.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.