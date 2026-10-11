//! Renders the window headlessly so its layout can be checked without a display.
use egui_kittest::Harness;

#[test]
fn gui_renders() {
    // build the app from the folder with the project's scripts, so the picker shows
    // something, then come back so the snapshot lands in tests/snapshots
    let here = std::env::current_dir().unwrap();
    for dir in ["../out", "out"] {
        if std::path::Path::new(dir).join("calibration.wwm.json").exists() {
            std::env::set_current_dir(dir).unwrap();
            break;
        }
    }
    let mut harness = Harness::builder()
        .with_size(egui::Vec2::new(560.0, 720.0))
        .build_eframe(|cc| wwm_play::gui::App::new(cc));
    std::env::set_current_dir(here).unwrap();
    harness.run();
    harness.snapshot("gui");
}
