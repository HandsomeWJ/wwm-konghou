//! Window lookup, focus, and timer resolution (Windows), with inert stand-ins elsewhere.

pub struct WindowInfo {
    #[cfg(windows)]
    pub hwnd: HWND,
    pub title: String,
    pub process: String,
    pub area: i64,
}

#[cfg(windows)]
pub use windows_sys::Win32::Foundation::HWND;
#[cfg(not(windows))]
pub type HWND = *mut core::ffi::c_void;

#[cfg(windows)]
mod imp {
    use super::{WindowInfo, HWND};
    use windows_sys::Win32::Foundation::{CloseHandle, BOOL, LPARAM, RECT};
    use windows_sys::Win32::Media::{timeBeginPeriod, timeEndPeriod};
    use windows_sys::Win32::System::Threading::{
        GetCurrentProcessId, GetCurrentThread, OpenProcess, QueryFullProcessImageNameW, SetThreadPriority,
        PROCESS_QUERY_LIMITED_INFORMATION, THREAD_PRIORITY_TIME_CRITICAL,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetForegroundWindow, GetWindowRect, GetWindowTextW, GetWindowThreadProcessId, IsIconic,
        IsWindowVisible, SetForegroundWindow, ShowWindow, SW_RESTORE,
    };

    pub fn precise_timers(on: bool) {
        unsafe {
            if on {
                timeBeginPeriod(1);
                SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL);
            } else {
                timeEndPeriod(1);
            }
        }
    }

    unsafe fn process_name(hwnd: HWND) -> String {
        let mut pid: u32 = 0;
        GetWindowThreadProcessId(hwnd, &mut pid);
        if pid == 0 || pid == GetCurrentProcessId() {
            return String::new();
        }
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return String::new();
        }
        let mut buf = [0u16; 1024];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(handle, 0, buf.as_mut_ptr(), &mut len);
        CloseHandle(handle);
        if ok == 0 {
            return String::new();
        }
        let path = String::from_utf16_lossy(&buf[..len as usize]);
        path.rsplit(['\\', '/']).next().unwrap_or("").to_string()
    }

    unsafe extern "system" fn collect(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let list = &mut *(lparam as *mut Vec<WindowInfo>);
        if IsWindowVisible(hwnd) == 0 {
            return 1;
        }
        let mut buf = [0u16; 256];
        let len = GetWindowTextW(hwnd, buf.as_mut_ptr(), buf.len() as i32);
        let title = String::from_utf16_lossy(&buf[..len.max(0) as usize]);
        let mut rect = RECT { left: 0, top: 0, right: 0, bottom: 0 };
        GetWindowRect(hwnd, &mut rect);
        let area = (rect.right - rect.left).max(0) as i64 * (rect.bottom - rect.top).max(0) as i64;
        list.push(WindowInfo { hwnd, title, process: process_name(hwnd), area });
        1
    }

    pub fn list_windows() -> Vec<WindowInfo> {
        let mut list: Vec<WindowInfo> = Vec::new();
        unsafe {
            EnumWindows(Some(collect), &mut list as *mut Vec<WindowInfo> as LPARAM);
        }
        list
    }

    pub fn focus_window(hwnd: HWND) -> bool {
        unsafe {
            if IsIconic(hwnd) != 0 {
                ShowWindow(hwnd, SW_RESTORE);
            }
            SetForegroundWindow(hwnd) != 0
        }
    }

    pub fn is_foreground(hwnd: HWND) -> bool {
        unsafe { GetForegroundWindow() == hwnd }
    }
}

#[cfg(not(windows))]
mod imp {
    use super::{WindowInfo, HWND};
    pub fn precise_timers(_on: bool) {}
    pub fn list_windows() -> Vec<WindowInfo> {
        Vec::new()
    }
    pub fn focus_window(_hwnd: HWND) -> bool {
        false
    }
    pub fn is_foreground(_hwnd: HWND) -> bool {
        true
    }
}

pub use imp::{focus_window, is_foreground, list_windows, precise_timers};

pub const KNOWN_PROCESSES: [&str; 3] = ["wwm.exe", "yysls.exe", "wherewindsmeet.exe"];

/// The game's main window as (handle, description): title contains `title_needle`, or
/// the process is one of the known game executables (or contains `process_needle`).
/// The largest matching window wins.
pub fn find_window(title_needle: &str, process_needle: Option<&str>) -> Option<(HWND, String)> {
    let title_needle = title_needle.to_lowercase();
    let process_needle = process_needle.map(|p| p.to_lowercase());
    list_windows()
        .into_iter()
        .filter(|w| {
            let proc_lc = w.process.to_lowercase();
            let by_title = !title_needle.is_empty() && w.title.to_lowercase().contains(&title_needle);
            let by_process = match &process_needle {
                Some(p) => !p.is_empty() && proc_lc.contains(p.as_str()),
                None => KNOWN_PROCESSES.contains(&proc_lc.as_str()),
            };
            by_title || by_process
        })
        .max_by_key(|w| w.area)
        .map(|w| {
            #[cfg(windows)]
            let h = w.hwnd;
            #[cfg(not(windows))]
            let h: HWND = std::ptr::null_mut();
            (h, format!("{} ({})", w.process, w.title))
        })
}
