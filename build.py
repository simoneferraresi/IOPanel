# build.py

import os
import subprocess
from pathlib import Path

# --- Configuration ---
PROJECT_ROOT = Path(__file__).parent
RESOURCES_DIR = PROJECT_ROOT / "resources"
QRC_FILE = RESOURCES_DIR / "resources.qrc"
COMPILED_RESOURCE_FILE = RESOURCES_DIR / "resources_rc.py"


def get_file_mtime(filepath):
    """Get the last modification time of a file."""
    try:
        return os.path.getmtime(filepath)
    except FileNotFoundError:
        return 0


def build_resources(force_build=False):
    """
    Builds the Qt resources file (resources_rc.py) if the QRC file
    or any of the icons are newer than the compiled file.
    """
    print("--- Checking Qt Resources ---")

    # Check if the compiled file exists
    compiled_mtime = get_file_mtime(COMPILED_RESOURCE_FILE)
    if compiled_mtime == 0:
        print(f"'{COMPILED_RESOURCE_FILE.name}' not found. A build is required.")
        force_build = True

    # Check if the QRC file is newer
    qrc_mtime = get_file_mtime(QRC_FILE)
    if qrc_mtime > compiled_mtime:
        print(
            f"'{QRC_FILE.name}' is newer than the compiled resource file. A build is required."
        )
        force_build = True

    # Check if any icon is newer than the compiled file
    if not force_build:
        icons_dir = RESOURCES_DIR / "icons"
        for icon_file in icons_dir.rglob("*"):
            if icon_file.is_file() and get_file_mtime(icon_file) > compiled_mtime:
                print(
                    f"Icon '{icon_file.name}' is newer than the compiled resource file. A build is required."
                )
                force_build = True
                break  # No need to check other icons

    if not force_build:
        print("✅ Resources are up-to-date. No build needed.")
        return

    print("Building resources...")
    # The -base flag tells rcc where to look for the relative paths (e.g., 'icons/play.svg')
    # The path provided to -base should be the directory containing the .qrc file.
    base_path = str(RESOURCES_DIR)

    # We provide absolute paths for the input and output files to be safe.
    input_qrc_file = str(QRC_FILE)
    output_py_file = str(COMPILED_RESOURCE_FILE)

    command = ["pyside6-rcc", "-base", base_path, input_qrc_file, "-o", output_py_file]

    try:
        # We run the command from the project root (no cwd change)
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("Errors/Warnings during build:")
            print(result.stderr)
        print("✅ Resources built successfully.")
    except FileNotFoundError:
        print("\nERROR: 'pyside6-rcc' command not found.")
        print(
            "Please ensure that PySide6 is installed and that its scripts directory is in your system's PATH."
        )
    except subprocess.CalledProcessError as e:
        # We can make the error message a bit more helpful
        print("\nERROR: The resource compiler failed with a non-zero exit code.")
        print(f"Working Directory: {RESOURCES_DIR}")
        print(f"Command: {' '.join(command)}")
        print(f"Return Code: {e.returncode}")
        print(f"Output:\n{e.stdout}")
        print(f"Error Output:\n{e.stderr}")
    except Exception as e:
        print(f"\nAn unexpected error occurred during the build: {e}")


if __name__ == "__main__":
    # You can add command-line arguments here if needed, e.g., --force
    import argparse

    parser = argparse.ArgumentParser(description="Build script for the Lab App.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force a rebuild of the resources even if they seem up-to-date.",
    )
    args = parser.parse_args()

    build_resources(force_build=args.force)
