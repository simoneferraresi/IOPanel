# Photonics Lab Control GUI

![App Screenshot](https//i.imgur.com/your-screenshot-url.png)  <!-- TODO: Replace with an actual screenshot of your application -->

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
    -   Allied Vision **Vimba SDK** for camera support.
    -   Yenista CT400 drivers and required libraries (e.g., NI-VISA for GPIB).
-   **(Optional) MATLAB:** Required only for saving scan plots as `.fig` files. You must also install the MATLAB Engine for Python.

### 2. Setting up the Environment with `uv`

This project uses [`uv`](https://github.com/astral-sh/uv), a fast Python package manager. It is highly recommended for managing the environment.

```bash
# 1. Clone the repository
git clone https://your-repo-url.git
cd photonics-lab-control

# 2. Create and activate a virtual environment using uv
# This command creates a virtual environment named .venv in the current directory
uv venv

# 3. Activate the environment
# On Windows (PowerShell)
.venv\Scripts\Activate.ps1
# On Windows (CMD)
.venv\Scripts\activate.bat
# On macOS/Linux
source .venv/bin/activate

# 4. Install dependencies
# uv will read the pyproject.toml file and install everything needed.
uv pip install -e .

### 4. (Optional) MATLAB Engine Setup

If you have MATLAB installed and want `.fig` export functionality, install the MATLAB Engine API for Python. Navigate to the appropriate MATLAB directory in your terminal and run the installation command.

**On Windows (example path):**
```powershell
cd "C:\Program Files\MATLAB\R2023b\extern\engines\python"
python setup.py install
```
[Official MATLAB Documentation for installation](https://www.mathworks.com/help/matlab/matlab_external/install-the-matlab-engine-for-python.html)

---

## Configuration

Before running the application for the first time, you must configure the `config.ini` file.

1.  **Copy the Template:** If `config.ini` does not exist, rename `config.template.ini` to `config.ini`.
2.  **Edit `config.ini`:**
    -   **`[Instruments]`**: Set the `ct400_dll_path` to the absolute path of your `CT400_lib.dll` file. Update the `tunics_gpib_address`.
    -   **`[Camera:*]`**: For each camera you want to use:
        -   Set `enabled = true`.
        -   Find the camera's unique `identifier` using a Vimba utility (or by running the app once, which lists available cameras in the log).
        -   Give it a descriptive `name`.

---

## Usage

Once the environment is set up and `config.ini` is configured, run the application from the project's root directory:

```bash
python app.py
```

### Command-line Arguments

You can override logging settings from the command line:

-   `--log-level`: Set the logging level (e.g., `DEBUG`, `INFO`).
-   `--log-file`: Specify a different path for the log file.
-   `--config`: Specify a different configuration file path.

Example:
```bash
python app.py --log-level DEBUG --config config.production.ini
```

---

## For Developers

### Project Structure

```
.
├── app.py                  # Main application entry point, argument parsing
├── main_window.py          # Main QMainWindow, orchestrates all UI components
├── control_panel.py        # UI panels for CT400 Scan and Power Monitor
├── gui_panels.py           # UI panels for Camera, Plot, and Histogram
├── camera.py               # Vimba camera abstraction class
├── CT400_updated.py        # Ctypes wrapper for the CT400 DLL
├── config_model.py         # Type-safe dataclass model for configuration
├── styles.py               # Global and component-specific QSS stylesheets
├── resources/              # Source icons and other assets
│   ├── icons/
│   │   └── laser.svg
│   └── resources.qrc       # Qt Resource Collection file
├── config.ini              # User configuration file (ignored by git)
├── config.template.ini     # A template for users to copy
├── requirements.txt        # Python package dependencies
└── README.md
```

### Generating `requirements.txt`

If you add or remove dependencies, update the `requirements.txt` file:

```bash
pip freeze > requirements.txt
```

### Compiling Qt Resources

The application uses icons stored in a Qt Resource File (`resources.qrc`). If you add or change icons in the `resources/` directory, you must recompile the `resources_rc.py` file.

```bash
# Make sure you have PySide6 installed
pyside6-rcc resources/resources.qrc -o resources_rc.py
```
Since `resources_rc.py` is in the `.gitignore`, you only need to do this when the source resources change.