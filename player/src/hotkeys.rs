//! Global hotkeys: F8 start/pause, F9 or Esc stop. Polled, so they work from any window.

#[derive(PartialEq, Clone, Copy, Debug)]
pub enum Hot {
    Toggle,
    Stop,
}

#[cfg(windows)]
mod imp {
    use super::Hot;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_ESCAPE, VK_F8, VK_F9};

    pub struct Hotkeys {
        was: [bool; 3],
    }

    impl Hotkeys {
        pub fn new() -> Self {
            Hotkeys { was: [false; 3] }
        }

        fn down(vk: u16) -> bool {
            (unsafe { GetAsyncKeyState(vk as i32) } as u16 & 0x8000) != 0
        }

        /// Rising edges only, so a held key fires once.
        pub fn poll(&mut self) -> Option<Hot> {
            let now = [Self::down(VK_F8), Self::down(VK_F9), Self::down(VK_ESCAPE)];
            let mut hit = None;
            if now[0] && !self.was[0] {
                hit = Some(Hot::Toggle);
            }
            if (now[1] && !self.was[1]) || (now[2] && !self.was[2]) {
                hit = Some(Hot::Stop);
            }
            self.was = now;
            hit
        }
    }
}

#[cfg(not(windows))]
mod imp {
    use super::Hot;

    pub struct Hotkeys;

    impl Hotkeys {
        pub fn new() -> Self {
            Hotkeys
        }
        pub fn poll(&mut self) -> Option<Hot> {
            None
        }
    }
}

pub use imp::Hotkeys;

impl Default for Hotkeys {
    fn default() -> Self {
        Hotkeys::new()
    }
}
