//! Keyboard back ends: real scan-code injection on Windows, a printing dry run anywhere.
use std::time::Instant;

pub trait Keyboard {
    /// Press (down=true) or release several keys in one batch, in order.
    fn send(&mut self, keys: &[(u16, bool)]);
}

pub struct DryRun {
    start: Instant,
}

impl DryRun {
    pub fn new() -> Self {
        DryRun { start: Instant::now() }
    }
}

impl Keyboard for DryRun {
    fn send(&mut self, keys: &[(u16, bool)]) {
        let t = self.start.elapsed().as_secs_f64();
        let list: Vec<String> = keys
            .iter()
            .map(|(sc, down)| format!("{}{:#04x}", if *down { "+" } else { "-" }, sc))
            .collect();
        println!("  {t:9.3}  {}", list.join(" "));
    }
}

#[cfg(windows)]
pub mod win {
    use super::Keyboard;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP,
        KEYEVENTF_SCANCODE,
    };

    pub struct ScanCodes;

    impl Keyboard for ScanCodes {
        fn send(&mut self, keys: &[(u16, bool)]) {
            if keys.is_empty() {
                return;
            }
            let inputs: Vec<INPUT> = keys
                .iter()
                .map(|&(sc, down)| {
                    let mut flags = KEYEVENTF_SCANCODE;
                    if !down {
                        flags |= KEYEVENTF_KEYUP;
                    }
                    if sc & 0xE000 == 0xE000 {
                        flags |= KEYEVENTF_EXTENDEDKEY;
                    }
                    INPUT {
                        r#type: INPUT_KEYBOARD,
                        Anonymous: INPUT_0 {
                            ki: KEYBDINPUT { wVk: 0, wScan: sc & 0xFF, dwFlags: flags, time: 0, dwExtraInfo: 0 },
                        },
                    }
                })
                .collect();
            let sent = unsafe { SendInput(inputs.len() as u32, inputs.as_ptr(), std::mem::size_of::<INPUT>() as i32) };
            if sent != inputs.len() as u32 {
                eprintln!("warning: SendInput delivered {sent}/{} events (is the game running as administrator?)", inputs.len());
            }
        }
    }
}
