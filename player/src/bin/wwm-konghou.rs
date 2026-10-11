//! Windowed player: double-click and go.
#![cfg_attr(windows, windows_subsystem = "windows")]

fn main() -> eframe::Result<()> {
    wwm_play::gui::run()
}
