import app
from config_model import AppConfig
from ui import main_window as main_window_module


def test_mainwindow_starts_offline_with_all_cameras_disabled(qtbot, monkeypatch, tmp_path):
    config_path = tmp_path / "disabled-cameras.ini"
    config_path.write_text(
        "[App]\nname = Disabled cameras startup test\n\n"
        "[Camera:Disabled camera]\nidentifier = disabled-camera\nenabled = false\nname = Disabled camera\n"
        "backend = vimba\n",
        encoding="utf-8",
    )
    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))

    # Keep startup independent from CT400 hardware and make any camera worker
    # construction or Vimba startup fail immediately in this regression.
    monkeypatch.setattr(main_window_module.MainWindow, "_init_ct400_lazy", lambda _window: None)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_start_vimbasystem",
        lambda _window: (_ for _ in ()).throw(AssertionError("Vimba must not start")),
    )
    monkeypatch.setattr(
        main_window_module,
        "CameraInitWorker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("camera worker must not start")),
    )

    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    assert window.histogram_plot_layout.spacing() == 4
    assert window.histogram_plot_layout.stretch(0) == 1
    window.show()

    qtbot.waitUntil(
        lambda: any(action.text() == "No enabled cameras found in config" for action in window.cameras_menu.actions()),
        timeout=2000,
    )
    placeholder = next(
        action for action in window.cameras_menu.actions() if action.text() == "No enabled cameras found in config"
    )
    assert not placeholder.isEnabled()
    assert window._init_tasks == set()

    window.close()
