//! The scheduler that turns a script into timed key presses, shared by the CLI and the GUI.
use std::collections::HashSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crate::hotkeys::{Hot, Hotkeys};
use crate::input::{DryRun, Keyboard};
use crate::platform::{self, HWND};
use crate::score::{Event as ScriptEvent, Script};

#[derive(Clone, Debug)]
pub struct PlayOptions {
    pub script_path: String,
    pub speed: f64,
    pub hold_ms: Option<u64>,
    pub modifier_settle_ms: u64,
    pub lead_in: f64,
    pub start_at: f64,
    pub window: String,
    pub process: Option<String>,
    pub focus: bool,
    pub background: bool,
    pub guard: bool,
    pub dry_run: bool,
    pub record: Option<String>,
    pub record_device: Option<String>,
}

impl Default for PlayOptions {
    fn default() -> Self {
        PlayOptions {
            script_path: String::new(),
            speed: 1.0,
            hold_ms: None,
            modifier_settle_ms: 50,
            lead_in: 3.0,
            start_at: 0.0,
            window: "Where Winds Meet".to_string(),
            process: None,
            focus: false,
            background: false,
            guard: true,
            dry_run: false,
            record: None,
            record_device: None,
        }
    }
}

#[derive(Clone, Debug)]
pub enum Event {
    Status(String),
    Progress { fraction: f32, position_s: f64, notes: String },
    Paused(bool),
    Recorded { path: String, seconds: f64 },
}

pub trait Sink: Send + Sync {
    fn emit(&self, e: Event);
}

/// Prints events to the terminal (the CLI's sink).
pub struct PrintSink;

impl Sink for PrintSink {
    fn emit(&self, e: Event) {
        use std::io::Write;
        match e {
            Event::Status(s) => println!("{s}"),
            Event::Progress { fraction, position_s, notes } => {
                let secs = position_s as u64;
                print!("\r  {:>3}% {:02}:{:02}  {:<40}", (fraction * 100.0) as u32, secs / 60, secs % 60, notes);
                let _ = std::io::stdout().flush();
            }
            Event::Paused(p) => println!("{}", if p { "paused (F8 to resume, F9 to stop)" } else { "resumed" }),
            Event::Recorded { path, seconds } => println!("recorded {seconds:.1}s to {path}"),
        }
    }
}

/// Start/pause/stop requests from the UI, merged with the global hotkeys.
pub struct Control {
    stop: AtomicBool,
    toggle: AtomicBool,
    hot: Mutex<Hotkeys>,
    use_hotkeys: bool,
}

impl Control {
    pub fn new(use_hotkeys: bool) -> Arc<Control> {
        Arc::new(Control {
            stop: AtomicBool::new(false),
            toggle: AtomicBool::new(false),
            hot: Mutex::new(Hotkeys::new()),
            use_hotkeys,
        })
    }

    pub fn request_stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub fn request_toggle(&self) {
        self.toggle.store(true, Ordering::SeqCst);
    }

    pub fn poll(&self) -> Option<Hot> {
        if self.stop.swap(false, Ordering::SeqCst) {
            return Some(Hot::Stop);
        }
        if self.toggle.swap(false, Ordering::SeqCst) {
            return Some(Hot::Toggle);
        }
        if self.use_hotkeys {
            return self.hot.lock().unwrap().poll();
        }
        None
    }
}

pub struct Outcome {
    pub stopped: bool,
    pub elapsed_s: f64,
    pub recorded: Option<(String, f64)>,
}

struct Pending {
    at: Instant,
    sc: u16,
    modifier: bool,
}

enum Flow {
    Go,
    Stop,
}

struct Player<'a> {
    script: &'a Script,
    kb: Box<dyn Keyboard>,
    control: Arc<Control>,
    sink: &'a dyn Sink,
    pending: Vec<Pending>,
    hold: Duration,
    settle: Duration,
    speed: f64,
    /// When set, playback auto-pauses while this window is not in front.
    guard: Option<HWND>,
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

    /// Sleep until `target`, servicing key releases and control. Returns Stop on F9/Esc.
    /// A pause releases everything and returns the time spent paused.
    fn wait_until(&mut self, target: Instant) -> (Flow, Duration) {
        let mut paused_total = Duration::ZERO;
        loop {
            let now = Instant::now();
            self.release_due(now);
            let hot = self.control.poll();
            let lost_focus = matches!(self.guard, Some(h) if !platform::is_foreground(h));
            match hot {
                Some(Hot::Stop) => return (Flow::Stop, paused_total),
                Some(Hot::Toggle) => {
                    self.release_all();
                    let pause_start = Instant::now();
                    self.sink.emit(Event::Paused(true));
                    loop {
                        std::thread::sleep(Duration::from_millis(10));
                        match self.control.poll() {
                            Some(Hot::Stop) => return (Flow::Stop, paused_total),
                            Some(Hot::Toggle) => break,
                            None => {}
                        }
                    }
                    paused_total += pause_start.elapsed();
                    self.sink.emit(Event::Paused(false));
                }
                None if lost_focus => {
                    self.release_all();
                    let pause_start = Instant::now();
                    self.sink.emit(Event::Status("game window not in front: paused (click into the game to resume)".into()));
                    loop {
                        std::thread::sleep(Duration::from_millis(50));
                        if let Some(Hot::Stop) = self.control.poll() {
                            return (Flow::Stop, paused_total);
                        }
                        if matches!(self.guard, Some(h) if platform::is_foreground(h)) {
                            break;
                        }
                    }
                    std::thread::sleep(Duration::from_millis(300)); // let the click that refocused the game settle
                    paused_total += pause_start.elapsed();
                    self.sink.emit(Event::Status("resumed".into()));
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

    fn play_event(&mut self, ev: &ScriptEvent) {
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

    fn run(&mut self, events: &[&ScriptEvent], lead_in: Duration, clock_zero: Instant) -> (Flow, Option<f64>) {
        let first_ms = events.first().map(|e| e.t_ms).unwrap_or(0);
        let last_ms = events.last().map(|e| e.t_ms).unwrap_or(0).max(first_ms + 1);
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
            if i % 4 == 0 || i + 1 == total {
                self.sink.emit(Event::Progress {
                    fraction: (ev.t_ms - first_ms) as f32 / (last_ms - first_ms) as f32,
                    position_s: (ev.t_ms - first_ms) as f64 / 1000.0,
                    notes: ev.notes.join(" "),
                });
            }
        }
        let end = self.pending.iter().map(|p| p.at).max().unwrap_or_else(Instant::now);
        let (flow, _) = self.wait_until(end + Duration::from_millis(5));
        self.release_all();
        (flow, first_sent)
    }
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

/// Describe the game window the options would use, without playing anything.
pub fn game_window(opts: &PlayOptions) -> Option<String> {
    platform::find_window(&opts.window, opts.process.as_deref()).map(|(_, d)| d)
}

/// Play the script described by `opts`. Blocks until the end, a stop, or an error.
/// The lead-in starts immediately; waiting for a hotkey is the caller's business.
pub fn play(opts: &PlayOptions, control: Arc<Control>, sink: &dyn Sink) -> Result<Outcome, String> {
    let script = Script::load(&opts.script_path)?;
    if !(0.1..=4.0).contains(&opts.speed) {
        return Err("speed must be between 0.1 and 4".into());
    }
    let start_ms = (opts.start_at.max(0.0) * 1000.0) as u64;
    let events: Vec<&ScriptEvent> = script.events.iter().filter(|e| e.t_ms >= start_ms).collect();
    if events.is_empty() {
        return Err(format!("nothing to play after {:.0}s", opts.start_at));
    }
    let dur = script.duration_ms() / 1000;
    sink.emit(Event::Status(format!(
        "{} onsets, {:02}:{:02}, mode {}, keymap {}",
        script.events.len(), dur / 60, dur % 60, script.mode, script.keymap.name
    )));

    let game = if opts.dry_run || !cfg!(windows) {
        None
    } else {
        platform::find_window(&opts.window, opts.process.as_deref())
    };
    if cfg!(windows) && !opts.dry_run {
        match &game {
            Some((_, desc)) => sink.emit(Event::Status(format!("game window: {desc}"))),
            None if opts.background => {
                return Err("background mode needs the game window, and none matched the title or the game process".into())
            }
            None => sink.emit(Event::Status("no game window found: keys go to whatever is in front".into())),
        }
    }

    let kb: Box<dyn Keyboard> = if opts.dry_run || !cfg!(windows) {
        if !opts.dry_run {
            sink.emit(Event::Status("not on Windows: dry run only".into()));
        }
        Box::new(DryRun::new())
    } else {
        #[cfg(windows)]
        {
            if opts.background {
                sink.emit(Event::Status("background mode: posting key messages to the game window".into()));
                Box::new(crate::input::win::WindowMessages { hwnd: game.as_ref().unwrap().0 })
            } else {
                Box::new(crate::input::win::ScanCodes)
            }
        }
        #[cfg(not(windows))]
        {
            unreachable!()
        }
    };

    let guard = if opts.background || !opts.guard { None } else { game.as_ref().map(|g| g.0) };
    let mut player = Player {
        script: &script,
        kb,
        control: control.clone(),
        sink,
        pending: Vec::new(),
        hold: Duration::from_millis(opts.hold_ms.unwrap_or(script.hold_ms)),
        settle: Duration::from_millis(opts.modifier_settle_ms),
        speed: opts.speed,
        guard,
    };

    if opts.focus {
        match &game {
            Some((h, _)) if platform::focus_window(*h) => sink.emit(Event::Status("brought the game window to the front".into())),
            _ => sink.emit(Event::Status("could not focus the game window; alt-tab into the game now".into())),
        }
    }
    platform::precise_timers(true);
    let lead = Duration::from_secs_f64(opts.lead_in.max(0.0));
    #[cfg(windows)]
    let recorder = match &opts.record {
        Some(_) if !opts.dry_run => match crate::record::Recorder::start(opts.record_device.as_deref()) {
            Ok(r) => {
                sink.emit(Event::Status(format!("recording {} ({} Hz)", r.device_name, r.sample_rate)));
                Some(r)
            }
            Err(e) => {
                platform::precise_timers(false);
                return Err(e);
            }
        },
        _ => None,
    };
    if lead > Duration::ZERO {
        sink.emit(Event::Status(format!("starting in {:.0}s ...", lead.as_secs_f64())));
    }
    let started = Instant::now();
    let (flow, first_sent) = player.run(&events, lead, started);
    let _ = &first_sent; // only the Windows recorder uses it
    platform::precise_timers(false);
    let stopped = matches!(flow, Flow::Stop);
    let elapsed_s = started.elapsed().as_secs_f64();
    sink.emit(Event::Status(if stopped { format!("stopped after {elapsed_s:.1}s") } else { format!("done in {elapsed_s:.1}s") }));

    #[allow(unused_mut)]
    let mut recorded = None;
    #[cfg(windows)]
    if let (Some(rec), Some(path)) = (recorder, &opts.record) {
        std::thread::sleep(Duration::from_millis(1500)); // let the last note ring out
        match rec.finish(path) {
            Ok((secs, elapsed)) => {
                if (secs - elapsed).abs() > 0.05 * elapsed {
                    sink.emit(Event::Status("warning: recording length differs from elapsed time; the capture device may have paused".into()));
                }
                let sidecar = format!("{path}.json");
                // the recorder started a hair before `started`; the first note went out at
                // `first_sent` after it, which includes any pause while the game was not in front
                let meta = serde_json::json!({
                    "script": opts.script_path,
                    "offset_s": first_sent.unwrap_or(lead.as_secs_f64()),
                    "lead_in_s": lead.as_secs_f64(),
                    "speed": opts.speed,
                    "seconds": secs,
                    "stopped_early": stopped,
                });
                if let Err(e) = std::fs::write(&sidecar, serde_json::to_string_pretty(&meta).unwrap()) {
                    sink.emit(Event::Status(format!("warning: cannot write {sidecar}: {e}")));
                }
                sink.emit(Event::Recorded { path: path.clone(), seconds: secs });
                recorded = Some((path.clone(), secs));
            }
            Err(e) => sink.emit(Event::Status(format!("recording error: {e}"))),
        }
    }
    Ok(Outcome { stopped, elapsed_s, recorded })
}
