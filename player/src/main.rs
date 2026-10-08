//! wwm-play: sends a .wwm.json key script to Where Winds Meet with frame-accurate timing.
mod input;
mod record;
mod score;

use std::collections::HashSet;
use std::io::Write;
use std::time::{Duration, Instant};

use clap::Parser;

use input::{DryRun, Keyboard};
use score::{Event, Script};

#[derive(Parser, Debug)]
#[command(name = "wwm-play", version, about = "Plays a .wwm.json key script into Where Winds Meet")]
struct Args {
    /// Script produced by `wwm arrange` (song.wwm.json)
    #[arg(required_unless_present_any = ["list_windows", "list_devices"])]
    script: Option<String>,
    /// Playback speed multiplier (0.5 = half speed)
    #[arg(long, default_value_t = 1.0)]
    speed: f64,
    /// Override how long each key is held, in ms (default: value in the script)
    #[arg(long)]
    hold_ms: Option<u64>,
    /// Delay between pressing Shift/Ctrl and the keys that need it, in ms (50 passed the in-game calibration; 25 dropped some)
    #[arg(long, default_value_t = 50)]
    modifier_settle_ms: u64,
    /// Seconds between the start signal and the first note (time to alt-tab into the game)
    #[arg(long, default_value_t = 3.0)]
    lead_in: f64,
    /// Skip into the song by this many seconds
    #[arg(long, default_value_t = 0.0)]
    start_at: f64,
    /// Game window title (substring, case-insensitive) used by --focus, --background and the foreground guard
    #[arg(long, default_value = "Where Winds Meet")]
    window: String,
    /// Game process name (substring, case-insensitive). Default matches wwm.exe / yysls.exe, the known game executables
    #[arg(long)]
    process: Option<String>,
    /// Print every visible window with its process name and size, then exit
    #[arg(long)]
    list_windows: bool,
    /// Bring the game window to the front before playing
    #[arg(long)]
    focus: bool,
    /// Post key messages to the game window instead of the foreground app, so the game can stay in the background
    #[arg(long)]
    background: bool,
    /// Keep sending even when the game window is not in front (default: auto-pause so keys never land in another app)
    #[arg(long)]
    no_guard: bool,
    /// Start immediately instead of waiting for F8
    #[arg(long)]
    now: bool,
    /// Print key events instead of sending them
    #[arg(long)]
    dry_run: bool,
    /// Record what the PC plays while the script runs into this WAV (plus a .json sidecar for `wwm verify`)
    #[arg(long)]
    record: Option<String>,
    /// Output device to record (name substring; default: the Windows default output device)
    #[arg(long)]
    record_device: Option<String>,
    /// Print the output devices that can be recorded, then exit
    #[arg(long)]
    list_devices: bool,
}

#[derive(PartialEq, Clone, Copy)]
#[allow(dead_code)] // the non-Windows build never produces hotkeys
enum Hot {
    Toggle,
    Stop,
}

#[cfg(windows)]
mod hotkeys {
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
mod hotkeys {
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

#[cfg(windows)]
mod platform {
    pub use windows_sys::Win32::Foundation::HWND;
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

    pub struct WindowInfo {
        pub hwnd: HWND,
        pub title: String,
        pub process: String,
        pub area: i64,
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

    const KNOWN_PROCESSES: [&str; 3] = ["wwm.exe", "yysls.exe", "wherewindsmeet.exe"];

    /// The game's main window: title contains `title_needle`, or the process is one of
    /// the known game executables (or contains `process_needle`). Largest window wins.
    pub fn find_window(title_needle: &str, process_needle: Option<&str>) -> Option<HWND> {
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
            .map(|w| w.hwnd)
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
mod platform {
    pub type HWND = *mut core::ffi::c_void;
    pub struct WindowInfo {
        pub title: String,
        pub process: String,
        pub area: i64,
    }
    pub fn precise_timers(_on: bool) {}
    pub fn list_windows() -> Vec<WindowInfo> {
        Vec::new()
    }
    pub fn find_window(_title: &str, _process: Option<&str>) -> Option<HWND> {
        None
    }
    pub fn focus_window(_hwnd: HWND) -> bool {
        false
    }
    pub fn is_foreground(_hwnd: HWND) -> bool {
        true
    }
}

struct Pending {
    at: Instant,
    sc: u16,
    modifier: bool,
}

struct Player<'a> {
    script: &'a Script,
    kb: Box<dyn Keyboard>,
    hot: hotkeys::Hotkeys,
    pending: Vec<Pending>,
    hold: Duration,
    settle: Duration,
    speed: f64,
    /// When set, playback auto-pauses while this window is not in front.
    guard: Option<platform::HWND>,
}

enum Flow {
    Go,
    Stop,
}

impl<'a> Player<'a> {
    fn release_due(&mut self, now: Instant) {
        self.release_where(|p| p.at <= now);
    }

    fn release_where(&mut self, pred: impl Fn(&Pending) -> bool) {
        let mut due: Vec<(bool, u16)> = Vec::new();
        self.pending.retain(|p| {
            if pred(p) {
                due.push((p.modifier, p.sc));
                false
            } else {
                true
            }
        });
        if due.is_empty() {
            return;
        }
        // keys first, modifiers last: the game sees Shift+key go up as a unit
        due.sort_by_key(|&(modifier, _)| modifier);
        let batch: Vec<(u16, bool)> = due.iter().map(|&(_, sc)| (sc, false)).collect();
        self.kb.send(&batch);
    }

    fn release_all(&mut self) {
        self.release_where(|_| true);
    }

    /// Sleep until `target`, servicing key releases and hotkeys. Returns Stop on F9/Esc.
    /// A pause (F8) releases everything and returns the time spent paused.
    fn wait_until(&mut self, target: Instant) -> (Flow, Duration) {
        let mut paused_total = Duration::ZERO;
        loop {
            let now = Instant::now();
            self.release_due(now);
            let hot = self.hot.poll();
            let lost_focus = matches!(self.guard, Some(h) if !platform::is_foreground(h));
            match hot {
                Some(Hot::Stop) => return (Flow::Stop, paused_total),
                Some(Hot::Toggle) => {
                    self.release_all();
                    let pause_start = Instant::now();
                    println!("paused (F8 to resume, F9 to stop)");
                    loop {
                        std::thread::sleep(Duration::from_millis(10));
                        match self.hot.poll() {
                            Some(Hot::Stop) => return (Flow::Stop, paused_total),
                            Some(Hot::Toggle) => break,
                            None => {}
                        }
                    }
                    paused_total += pause_total_since(pause_start);
                    println!("resumed");
                }
                None if lost_focus => {
                    self.release_all();
                    let pause_start = Instant::now();
                    println!("game window not in front: paused (click into the game to resume, F9 to stop)");
                    loop {
                        std::thread::sleep(Duration::from_millis(50));
                        if let Some(Hot::Stop) = self.hot.poll() {
                            return (Flow::Stop, paused_total);
                        }
                        if matches!(self.guard, Some(h) if platform::is_foreground(h)) {
                            break;
                        }
                    }
                    std::thread::sleep(Duration::from_millis(300)); // let the click that refocused the game settle
                    paused_total += pause_total_since(pause_start);
                    println!("resumed");
                }
                None => {}
            }
            let now = Instant::now();
            let target = target + paused_total;
            if now >= target {
                return (Flow::Go, paused_total);
            }
            let remaining = target - now;
            if remaining > Duration::from_millis(4) {
                let next_release = self.pending.iter().map(|p| p.at).min();
                let mut nap = remaining - Duration::from_millis(3);
                if let Some(r) = next_release {
                    if r > now {
                        nap = nap.min(r - now);
                    } else {
                        nap = Duration::from_micros(200);
                    }
                }
                std::thread::sleep(nap.min(Duration::from_millis(5)));
            } else {
                std::hint::spin_loop();
            }
        }
    }

    fn play_event(&mut self, ev: &Event) {
        let codes = &self.script.keymap.scancodes;
        let mut in_event: HashSet<u16> = HashSet::new();
        for g in &ev.groups {
            for k in &g.keys {
                in_event.insert(codes[k]);
            }
        }
        // a repeat of a still-held key must go up first; a held modifier must never leak
        self.release_where(|p| p.modifier || in_event.contains(&p.sc));
        for g in &ev.groups {
            let keys: Vec<u16> = g.keys.iter().map(|k| codes[k]).collect();
            let downs: Vec<(u16, bool)> = keys.iter().map(|&sc| (sc, true)).collect();
            match &g.modifier {
                None => {
                    self.kb.send(&downs);
                    let at = Instant::now() + self.hold;
                    self.pending.extend(keys.iter().map(|&sc| Pending { at, sc, modifier: false }));
                }
                Some(m) => {
                    let msc = codes[m];
                    self.kb.send(&[(msc, true)]);
                    spin_sleep(self.settle);
                    self.kb.send(&downs);
                    let at = Instant::now() + self.hold;
                    self.pending.extend(keys.iter().map(|&sc| Pending { at, sc, modifier: false }));
                    self.pending.push(Pending { at: at + Duration::from_millis(3), sc: msc, modifier: true });
                }
            }
        }
    }

    fn run(&mut self, events: &[&Event], lead_in: Duration, clock_zero: Instant) -> (Flow, Option<f64>) {
        let first_ms = events.first().map(|e| e.t_ms).unwrap_or(0);
        let mut origin = Instant::now() + lead_in;
        let total = events.len();
        let mut first_sent: Option<f64> = None;
        for (i, ev) in events.iter().enumerate() {
            let has_natural = ev.groups.iter().any(|g| g.modifier.is_none());
            let has_modifier = ev.groups.iter().any(|g| g.modifier.is_some());
            // sharp-only onsets press Shift early so the note itself lands on time
            let pre = if has_modifier && !has_natural { self.settle } else { Duration::ZERO };
            let rel = Duration::from_secs_f64((ev.t_ms - first_ms) as f64 / 1000.0 / self.speed);
            let target = (origin + rel).checked_sub(pre).unwrap_or(origin);
            let (flow, paused) = self.wait_until(target);
            origin += paused;
            if let Flow::Stop = flow {
                self.release_all();
                return (Flow::Stop, first_sent);
            }
            if first_sent.is_none() {
                first_sent = Some(clock_zero.elapsed().as_secs_f64());
            }
            self.play_event(ev);
            if i % 8 == 0 || i + 1 == total {
                let secs = (ev.t_ms - first_ms) / 1000;
                print!("\r  {:>3}% {:02}:{:02}  {:<40}", (i + 1) * 100 / total, secs / 60, secs % 60, ev.notes.join(" "));
                let _ = std::io::stdout().flush();
            }
        }
        let end = self.pending.iter().map(|p| p.at).max().unwrap_or_else(Instant::now);
        let (flow, _) = self.wait_until(end + Duration::from_millis(5));
        self.release_all();
        println!();
        (flow, first_sent)
    }
}

fn pause_total_since(start: Instant) -> Duration {
    start.elapsed()
}

fn spin_sleep(d: Duration) {
    let target = Instant::now() + d;
    if d > Duration::from_millis(3) {
        std::thread::sleep(d - Duration::from_millis(2));
    }
    while Instant::now() < target {
        std::hint::spin_loop();
    }
}

fn main() {
    let args = Args::parse();
    if args.list_devices {
        #[cfg(windows)]
        for name in record::list_devices() {
            println!("{name}");
        }
        #[cfg(not(windows))]
        println!("not on Windows: no devices");
        return;
    }
    if args.list_windows {
        println!("{:<28} {:<10} title", "process", "size");
        for w in platform::list_windows() {
            println!("{:<28} {:<10} {}", w.process, w.area, w.title);
        }
        return;
    }
    let script_path = args.script.clone().unwrap_or_default();
    let script = match Script::load(&script_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(2);
        }
    };
    if !(0.1..=4.0).contains(&args.speed) {
        eprintln!("error: --speed must be between 0.1 and 4");
        std::process::exit(2);
    }
    let start_ms = (args.start_at * 1000.0) as u64;
    let events: Vec<&Event> = script.events.iter().filter(|e| e.t_ms >= start_ms).collect();
    if events.is_empty() {
        eprintln!("error: nothing to play after --start-at {}", args.start_at);
        std::process::exit(2);
    }
    let dur = script.duration_ms() / 1000;
    println!(
        "wwm-play: {} ({} onsets, {:02}:{:02}, mode {}, keymap {})",
        script_path, script.events.len(), dur / 60, dur % 60, script.mode, script.keymap.name
    );

    let game = if args.dry_run || !cfg!(windows) {
        None
    } else {
        platform::find_window(&args.window, args.process.as_deref())
    };
    if cfg!(windows) && !args.dry_run {
        match game {
            Some(_) => println!("game window found (title {:?} or process {})", args.window, args.process.as_deref().unwrap_or("wwm.exe/yysls.exe")),
            None if args.background => {
                eprintln!("error: --background needs the game window; nothing matched title {:?} or the game process. Run with --list-windows to see names, then pass --process <name>", args.window);
                std::process::exit(2);
            }
            None => println!("warning: no window matched title {:?} or the game process; keys go to whatever is in front (--list-windows shows names)", args.window),
        }
    }

    let kb: Box<dyn Keyboard> = if args.dry_run || !cfg!(windows) {
        if !args.dry_run {
            println!("not on Windows: dry run only");
        }
        Box::new(DryRun::new())
    } else {
        #[cfg(windows)]
        {
            if args.background {
                println!("background mode: posting key messages to the game window");
                Box::new(input::win::WindowMessages { hwnd: game.unwrap() })
            } else {
                Box::new(input::win::ScanCodes)
            }
        }
        #[cfg(not(windows))]
        {
            unreachable!()
        }
    };

    let guard = if args.background || args.no_guard { None } else { game };
    let mut player = Player {
        script: &script,
        kb,
        hot: hotkeys::Hotkeys::new(),
        pending: Vec::new(),
        hold: Duration::from_millis(args.hold_ms.unwrap_or(script.hold_ms)),
        settle: Duration::from_millis(args.modifier_settle_ms),
        speed: args.speed,
        guard,
    };

    let wait_for_hotkey = cfg!(windows) && !args.now && !args.dry_run;
    if wait_for_hotkey {
        println!("open the Konghou in free play, then press F8 to start (F8 pause, F9/Esc stop)");
        loop {
            std::thread::sleep(Duration::from_millis(10));
            match player.hot.poll() {
                Some(Hot::Toggle) => break,
                Some(Hot::Stop) => return,
                None => {}
            }
        }
    }
    if args.focus {
        match game {
            Some(h) if platform::focus_window(h) => println!("brought the game window to the front"),
            _ => println!("warning: could not focus the game window; alt-tab into the game now"),
        }
    }
    platform::precise_timers(true);
    let lead = Duration::from_secs_f64(args.lead_in.max(0.0));
    #[cfg(windows)]
    let recorder = match &args.record {
        Some(_) if !args.dry_run => match record::Recorder::start(args.record_device.as_deref()) {
            Ok(r) => {
                println!("recording {} ({} Hz) ...", r.device_name, r.sample_rate);
                Some(r)
            }
            Err(e) => {
                eprintln!("error: {e}");
                std::process::exit(2);
            }
        },
        _ => None,
    };
    if lead > Duration::ZERO {
        println!("starting in {:.0}s ...", lead.as_secs_f64());
    }
    let started = Instant::now();
    #[cfg(windows)]
    if let Some(r) = &recorder {
        println!("recorder running for {:.2}s before the clock started", r.elapsed().as_secs_f64());
    }
    let (flow, first_sent) = player.run(&events, lead, started);
    let _ = &first_sent; // only the Windows recorder uses it
    match flow {
        Flow::Stop => println!("stopped after {:.1}s", started.elapsed().as_secs_f64()),
        Flow::Go => println!("done in {:.1}s", started.elapsed().as_secs_f64()),
    }
    platform::precise_timers(false);
    #[cfg(windows)]
    if let (Some(rec), Some(path)) = (recorder, &args.record) {
        std::thread::sleep(Duration::from_millis(1500)); // let the last note ring out
        match rec.finish(path) {
            Ok((secs, elapsed)) => {
                println!("recorded {secs:.1}s to {path} (elapsed {elapsed:.1}s)");
                if (secs - elapsed).abs() > 0.05 * elapsed {
                    println!("warning: recording length differs from elapsed time; the capture device may have paused");
                }
                let sidecar = format!("{path}.json");
                // the recorder started a hair before `started`; the first note went out at
                // `first_sent` after it, which includes any pause while the game was not in front
                let meta = serde_json::json!({
                    "script": script_path,
                    "offset_s": first_sent.unwrap_or(lead.as_secs_f64()),
                    "lead_in_s": lead.as_secs_f64(),
                    "speed": args.speed,
                    "seconds": secs,
                    "stopped_early": matches!(flow, Flow::Stop),
                });
                if let Err(e) = std::fs::write(&sidecar, serde_json::to_string_pretty(&meta).unwrap()) {
                    eprintln!("warning: cannot write {sidecar}: {e}");
                } else {
                    println!("wrote {sidecar}");
                }
            }
            Err(e) => eprintln!("error: {e}"),
        }
    }
}
