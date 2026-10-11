//! Command-line player: `wwm-play.exe song.wwm.json [options]`.
use std::time::Duration;

use clap::Parser;
use wwm_play::engine::{self, Control, PlayOptions, PrintSink};
use wwm_play::hotkeys::Hot;
use wwm_play::platform;

#[derive(Parser, Debug)]
#[command(
    name = "wwm-play",
    version,
    about = "Plays a .wwm.json key script into Where Winds Meet",
    after_help = "Created by HandsomeWJ · https://github.com/HandsomeWJ/wwm-konghou"
)]
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

fn main() {
    let args = Args::parse();
    if args.list_devices {
        #[cfg(windows)]
        for name in wwm_play::record::list_devices() {
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
    let opts = PlayOptions {
        script_path: args.script.clone().unwrap_or_default(),
        speed: args.speed,
        hold_ms: args.hold_ms,
        modifier_settle_ms: args.modifier_settle_ms,
        lead_in: args.lead_in,
        start_at: args.start_at,
        window: args.window.clone(),
        process: args.process.clone(),
        focus: args.focus,
        background: args.background,
        guard: !args.no_guard,
        dry_run: args.dry_run,
        record: args.record.clone(),
        record_device: args.record_device.clone(),
    };
    let control = Control::new(true);
    let wait_for_hotkey = cfg!(windows) && !args.now && !args.dry_run;
    println!("wwm-play v{} · created by HandsomeWJ · https://github.com/HandsomeWJ/wwm-konghou", env!("CARGO_PKG_VERSION"));
    if wait_for_hotkey {
        println!("script: {}", opts.script_path);
        println!("open the Konghou in free play, then press F8 to start (F8 pause, F9/Esc stop)");
        loop {
            std::thread::sleep(Duration::from_millis(10));
            match control.poll() {
                Some(Hot::Toggle) => break,
                Some(Hot::Stop) => return,
                None => {}
            }
        }
    }
    match engine::play(&opts, control, &PrintSink) {
        Ok(_) => {}
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(2);
        }
    }
}
