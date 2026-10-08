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
    use windows_sys::Win32::Foundation::HWND;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        MapVirtualKeyW, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY,
        KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, MAPVK_VSC_TO_VK_EX,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{PostMessageW, WM_KEYDOWN, WM_KEYUP};

    /// Background mode: key messages posted straight to the game window, so it
    /// need not be in front. Works for games that read WM_KEYDOWN/WM_KEYUP; a game
    /// that polls the keyboard state or raw input ignores these.
    pub struct WindowMessages {
        pub hwnd: HWND,
    }

    impl Keyboard for WindowMessages {
        fn send(&mut self, keys: &[(u16, bool)]) {
            for &(sc, down) in keys {
                let extended = sc & 0xE000 == 0xE000;
                let code = (sc & 0xFF) as u32;
                let vk = unsafe { MapVirtualKeyW(if extended { 0xE000 | code } else { code }, MAPVK_VSC_TO_VK_EX) };
                let mut lparam: usize = 1 | (code as usize) << 16;
                if extended {
                    lparam |= 1 << 24;
                }
                if !down {
                    lparam |= (1 << 30) | (1 << 31);
                }
                let msg = if down { WM_KEYDOWN } else { WM_KEYUP };
                let ok = unsafe { PostMessageW(self.hwnd, msg, vk as usize, lparam as isize) };
                if ok == 0 {
                    eprintln!("warning: PostMessage failed for scan code {sc:#04x} (window gone?)");
                }
            }
        }
    }

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
