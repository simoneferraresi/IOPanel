import app
from config_model import AppConfig
from hardware import camera as camera_module
from hardware import ct400_init_worker
from hardware.simulated_camera import SimulatedCamera
from ui import main_window as main_window_module


def _runner_stopped(task):
    try:
        return not task.thread.isRunning()
    except RuntimeError:
        return True


def _start_with_config(qtbot, monkeypatch, tmp_path, camera_sections):
    config_path = tmp_path / "camera-failures.ini"
    config_path.write_text(
        "[App]\nname = Camera failure integration test\n\n"
        "[Instruments]\nct400_dll_path = unavailable-in-test-environment.dll\n\n" + "\n\n".join(camera_sections),
        encoding="utf-8",
    )
    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))

    # Ensure this test cannot load a CT400 driver even on a laboratory PC.
    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", lambda _worker: None)

    # Physical-camera startup is replaced with a guard. CameraInitWorker still
    # follows its production path, but no Vimba system context can be entered.
    vimba_start_attempts = []
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_start_vimbasystem",
        lambda _window: vimba_start_attempts.append(True),
    )

    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    window.show()
    return window, vimba_start_attempts


def _wait_for_camera_tasks(qtbot, window):
    qtbot.waitUntil(
        lambda: all(_runner_stopped(task) for task in window._init_tasks),
        timeout=4000,
    )


def test_mainwindow_reports_configured_camera_when_vimba_is_unavailable(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(camera_module, "VIMBA_AVAILABLE", False)
    window, vimba_start_attempts = _start_with_config(
        qtbot,
        monkeypatch,
        tmp_path,
        ["[Camera:Lab camera]\nidentifier = physical-camera-1\nenabled = true\nname = Lab camera"],
    )

    qtbot.waitUntil(lambda: "physical-camera-1" in window.camera_panels, timeout=4000)
    qtbot.waitUntil(
        lambda: "Failed to Open" in window.camera_panels["physical-camera-1"].video_label.text(),
        timeout=4000,
    )
    _wait_for_camera_tasks(qtbot, window)

    assert vimba_start_attempts == [True]  # The guarded hook was called, never the physical SDK.
    assert not window.cameras
    assert "Lab camera" in window.camera_panels["physical-camera-1"].video_label.text()
    assert "[SIMULATED]" not in window.camera_panels["physical-camera-1"].video_label.text()

    window.close()


def test_mainwindow_keeps_simulated_camera_when_physical_initialization_fails(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(camera_module, "VIMBA_AVAILABLE", True)
    open_attempts = []

    def fail_physical_open(camera):
        open_attempts.append(camera.identifier)
        camera.error.emit("injected physical camera open failure")
        return False

    # VimbaCam.open is replaced before it can access any SDK. Discovery and
    # system initialization are also kept behind the guarded hook above.
    monkeypatch.setattr(camera_module.VimbaCam, "open", fail_physical_open)
    window, vimba_start_attempts = _start_with_config(
        qtbot,
        monkeypatch,
        tmp_path,
        [
            "[Camera:Simulated camera]\nidentifier = simulated-top\nenabled = true\nname = Simulated top\n"
            "backend = simulation\nsimulation_width = 16\nsimulation_height = 12",
            "[Camera:Physical camera]\nidentifier = physical-camera-2\nenabled = true\nname = Physical camera",
        ],
    )

    qtbot.waitUntil(lambda: len(window.cameras) == 1, timeout=4000)
    qtbot.waitUntil(
        lambda: "Failed to Open" in window.camera_panels["physical-camera-2"].video_label.text(),
        timeout=4000,
    )
    _wait_for_camera_tasks(qtbot, window)

    assert vimba_start_attempts == [True]
    assert open_attempts == ["physical-camera-2"]
    assert len(window.cameras) == 1
    assert isinstance(window.cameras[0], SimulatedCamera)
    assert "[SIMULATED]" in window.camera_panels["simulated-top"].title_label.text()
    assert "Physical camera" in window.camera_panels["physical-camera-2"].video_label.text()
    qtbot.waitUntil(lambda: window.camera_panels["simulated-top"]._latest_pixmap is not None, timeout=2000)
    assert not window.camera_panels["simulated-top"]._latest_pixmap.isNull()

    camera = window.cameras[0]
    frame_thread = camera._frame_thread
    window.close()
    qtbot.waitUntil(lambda: not frame_thread.is_alive(), timeout=1500)
    assert not window.cameras
