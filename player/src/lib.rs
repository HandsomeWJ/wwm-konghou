//! wwm-play: sends .wwm.json key scripts to Where Winds Meet with frame-accurate timing.
pub mod engine;
pub mod gui;
pub mod hotkeys;
pub mod input;
pub mod platform;
#[cfg(windows)]
pub mod record;
pub mod score;
