//! Window for the player: pick a script, set the few options that matter, start, watch.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use eframe::egui;
use crate::engine::{self, Control, Event, PlayOptions, Sink};
use crate::platform;
use crate::score::Script;

pub const CREATOR: &str = "HandsomeWJ";
pub const REPO_URL: &str = "https://github.com/HandsomeWJ/wwm-konghou";

enum Msg {
    Ev(Event),
    Done(Result<(bool, f64), String>),
}

struct ChannelSink(Mutex<Sender<Msg>>);

impl Sink for ChannelSink {
    fn emit(&self, e: Event) {
        let _ = self.0.lock().unwrap().send(Msg::Ev(e));
    }
}

struct Running {
    control: Arc<Control>,
    rx: Receiver<Msg>,
    handle: Option<JoinHandle<()>>,
}

#[derive(Clone)]
struct ScriptInfo {
    onsets: usize,
    duration_s: u64,
    mode: String,
    keymap: String,
    with_modifiers: usize,
}

pub struct App {
    scripts: Vec<PathBuf>,
    selected: Option<PathBuf>,
    info: Option<ScriptInfo>,
    opts: PlayOptions,
    record: bool,
    record_name: String,
    devices: Vec<String>,
    device_idx: usize,
    game: Option<String>,
    windows: Vec<(String, String, i64)>,
    show_windows: bool,
    always_on_top: bool,
    running: Option<Running>,
    paused: bool,
    progress: f32,
    position_s: f64,
    notes: String,
    log: VecDeque<String>,
    last_error: Option<String>,
}

fn script_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        dirs.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let dir = dir.to_path_buf();
            if !dirs.contains(&dir) {
                dirs.push(dir);
            }
        }
    }
    dirs
}

fn find_scripts() -> Vec<PathBuf> {
    let mut out = Vec::new();
    for dir in script_dirs() {
        if let Ok(entries) = std::fs::read_dir(&dir) {
            for e in entries.flatten() {
                let p = e.path();
                if p.file_name().map(|n| n.to_string_lossy().ends_with(".wwm.json")).unwrap_or(false) && !out.contains(&p) {
                    out.push(p);
                }
            }
        }
    }
    out.sort();
    out
}

fn load_info(path: &Path) -> Result<ScriptInfo, String> {
    let s = Script::load(&path.to_string_lossy())?;
    Ok(ScriptInfo {
        onsets: s.events.len(),
        duration_s: s.duration_ms() / 1000,
        mode: s.mode.clone(),
        keymap: s.keymap.name.clone(),
        with_modifiers: s.events.iter().filter(|e| e.groups.iter().any(|g| g.modifier.is_some())).count(),
    })
}

fn install_cjk_font(ctx: &egui::Context) {
    // window titles can be Chinese; egui's bundled font has no CJK glyphs
    let candidates = [
        "C:\\Windows\\Fonts\\msyh.ttc",
        "C:\\Windows\\Fonts\\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ];
    for path in candidates {
        if let Ok(bytes) = std::fs::read(path) {
            let mut fonts = egui::FontDefinitions::default();
            fonts.font_data.insert("cjk".to_owned(), Arc::new(egui::FontData::from_owned(bytes)));
            for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
                fonts.families.entry(family).or_default().push("cjk".to_owned());
            }
            ctx.set_fonts(fonts);
            return;
        }
    }
}

impl App {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        install_cjk_font(&cc.egui_ctx);
        let scripts = find_scripts();
        let mut app = App {
            scripts: scripts.clone(),
            selected: None,
            info: None,
            opts: PlayOptions::default(),
            record: false,
            record_name: "take.wav".to_string(),
            devices: Vec::new(),
            device_idx: 0,
            game: None,
            windows: Vec::new(),
            show_windows: false,
            always_on_top: false,
            running: None,
            paused: false,
            progress: 0.0,
            position_s: 0.0,
            notes: String::new(),
            log: VecDeque::new(),
            last_error: None,
        };
        if let Some(first) = scripts.first().cloned() {
            app.select(first);
        }
        app.refresh_game();
        app.refresh_devices();
        app
    }

    fn select(&mut self, path: PathBuf) {
        match load_info(&path) {
            Ok(info) => {
                self.info = Some(info);
                self.last_error = None;
                let stem = path.file_name().map(|n| n.to_string_lossy().replace(".wwm.json", "")).unwrap_or_default();
                self.record_name = format!("{stem}.wav");
            }
            Err(e) => {
                self.info = None;
                self.last_error = Some(e);
            }
        }
        self.selected = Some(path);
    }

    fn refresh_game(&mut self) {
        self.game = engine::game_window(&self.opts);
        self.windows = platform::list_windows().into_iter().map(|w| (w.process, w.title, w.area)).collect();
    }

    fn refresh_devices(&mut self) {
        #[cfg(windows)]
        {
            self.devices = crate::record::list_devices();
        }
        if self.device_idx > self.devices.len() {
            self.device_idx = 0;
        }
    }

    fn push_log(&mut self, line: String) {
        self.log.push_back(line);
        while self.log.len() > 12 {
            self.log.pop_front();
        }
    }

    fn start(&mut self) {
        let Some(path) = self.selected.clone() else { return };
        let mut opts = self.opts.clone();
        opts.script_path = path.to_string_lossy().to_string();
        opts.record = if self.record { Some(self.record_name.clone()) } else { None };
        opts.record_device = if self.device_idx == 0 {
            None
        } else {
            self.devices.get(self.device_idx - 1).map(|d| d.trim_end_matches("  (default)").to_string())
        };
        opts.dry_run = !cfg!(windows);
        let control = Control::new(true);
        let (tx, rx) = channel();
        let ctl = control.clone();
        let handle = std::thread::spawn(move || {
            let sink = ChannelSink(Mutex::new(tx.clone()));
            let result = engine::play(&opts, ctl, &sink).map(|o| (o.stopped, o.elapsed_s));
            let _ = tx.send(Msg::Done(result));
        });
        self.running = Some(Running { control, rx, handle: Some(handle) });
        self.paused = false;
        self.progress = 0.0;
        self.position_s = 0.0;
        self.notes.clear();
        self.last_error = None;
        self.push_log(format!("playing {}", path.file_name().unwrap_or_default().to_string_lossy()));
    }

    fn drain(&mut self) {
        let mut finished = false;
        let mut lines: Vec<String> = Vec::new();
        if let Some(run) = &self.running {
            while let Ok(msg) = run.rx.try_recv() {
                match msg {
                    Msg::Ev(Event::Status(s)) => lines.push(s),
                    Msg::Ev(Event::Progress { fraction, position_s, notes }) => {
                        self.progress = fraction;
                        self.position_s = position_s;
                        self.notes = notes;
                    }
                    Msg::Ev(Event::Paused(p)) => {
                        self.paused = p;
                        lines.push(if p { "paused".into() } else { "resumed".into() });
                    }
                    Msg::Ev(Event::Recorded { path, seconds }) => lines.push(format!("recorded {seconds:.1}s to {path} (+ .json)")),
                    Msg::Done(Ok((stopped, elapsed))) => {
                        lines.push(if stopped { format!("stopped after {elapsed:.0}s") } else { format!("finished, {elapsed:.0}s") });
                        finished = true;
                    }
                    Msg::Done(Err(e)) => {
                        self.last_error = Some(e.clone());
                        lines.push(format!("error: {e}"));
                        finished = true;
                    }
                }
            }
        }
        for l in lines {
            self.push_log(l);
        }
        if finished {
            if let Some(mut run) = self.running.take() {
                if let Some(h) = run.handle.take() {
                    let _ = h.join();
                }
            }
            self.paused = false;
        }
    }
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drain();
        if self.running.is_some() {
            ctx.request_repaint_after(Duration::from_millis(100));
        }
        let dropped: Vec<PathBuf> = ctx.input(|i| i.raw.dropped_files.iter().filter_map(|f| f.path.clone()).collect());
        if let Some(p) = dropped.into_iter().find(|p| p.to_string_lossy().ends_with(".wwm.json")) {
            if !self.scripts.contains(&p) {
                self.scripts.push(p.clone());
            }
            self.select(p);
        }
        let playing = self.running.is_some();

        egui::TopBottomPanel::top("head").show(ctx, |ui| {
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                ui.heading("WWM Konghou Player");
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let (text, color) = if self.paused {
                        ("PAUSED", egui::Color32::from_rgb(230, 160, 40))
                    } else if playing {
                        ("PLAYING", egui::Color32::from_rgb(60, 180, 90))
                    } else {
                        ("IDLE", egui::Color32::GRAY)
                    };
                    ui.colored_label(color, egui::RichText::new(text).strong());
                    if ui.checkbox(&mut self.always_on_top, "keep on top").changed() {
                        let level = if self.always_on_top { egui::WindowLevel::AlwaysOnTop } else { egui::WindowLevel::Normal };
                        ctx.send_viewport_cmd(egui::ViewportCommand::WindowLevel(level));
                    }
                });
            });
            ui.add_space(6.0);
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                ui.style_mut().spacing.slider_width = 240.0;
                let full = ui.available_width() - 16.0;
                ui.add_enabled_ui(!playing, |ui| {
                    ui.group(|ui| {
                        ui.set_min_width(full);
                        ui.label(egui::RichText::new("Script").strong());
                        ui.horizontal(|ui| {
                            let current = self
                                .selected
                                .as_ref()
                                .map(|p| p.file_name().unwrap_or_default().to_string_lossy().to_string())
                                .unwrap_or_else(|| "choose a .wwm.json".to_string());
                            let mut pick: Option<PathBuf> = None;
                            egui::ComboBox::from_id_salt("script").width(300.0).selected_text(current).show_ui(ui, |ui| {
                                for p in &self.scripts {
                                    let name = p.file_name().unwrap_or_default().to_string_lossy().to_string();
                                    if ui.selectable_label(self.selected.as_ref() == Some(p), name).clicked() {
                                        pick = Some(p.clone());
                                    }
                                }
                            });
                            if let Some(p) = pick {
                                self.select(p);
                            }
                            if ui.button("Browse…").clicked() {
                                let mut dlg = rfd::FileDialog::new().add_filter("WWM script", &["json"]);
                                if let Some(dir) = script_dirs().first() {
                                    dlg = dlg.set_directory(dir);
                                }
                                if let Some(p) = dlg.pick_file() {
                                    if !self.scripts.contains(&p) {
                                        self.scripts.push(p.clone());
                                    }
                                    self.select(p);
                                }
                            }
                            if ui.button("↻").on_hover_text("rescan this folder").clicked() {
                                self.scripts = find_scripts();
                            }
                        });
                        match &self.info {
                            Some(i) => {
                                ui.label(format!(
                                    "{} onsets · {:02}:{:02} · {} need Shift/Ctrl · {}-key · keymap {}",
                                    i.onsets, i.duration_s / 60, i.duration_s % 60, i.with_modifiers, i.mode, i.keymap
                                ));
                            }
                            None => {
                                ui.label("drop a .wwm.json here, or put it next to the exe and rescan");
                            }
                        }
                    });

                    ui.add_space(6.0);
                    ui.group(|ui| {
                        ui.set_min_width(full);
                        ui.label(egui::RichText::new("Game").strong());
                        ui.horizontal(|ui| {
                            match &self.game {
                                Some(d) => {
                                    ui.colored_label(egui::Color32::from_rgb(60, 180, 90), "●");
                                    ui.label(format!("window found: {d}"));
                                }
                                None => {
                                    ui.colored_label(egui::Color32::from_rgb(220, 80, 60), "●");
                                    ui.label("game window not found (keys go to whatever is in front)");
                                }
                            }
                            if ui.button("Refresh").clicked() {
                                self.refresh_game();
                            }
                        });
                        ui.horizontal(|ui| {
                            ui.checkbox(&mut self.opts.background, "play in the background")
                                .on_hover_text("post keys straight to the game window; it may sit behind other windows but not minimised");
                            ui.checkbox(&mut self.opts.focus, "bring game to front first");
                            ui.add_enabled(!self.opts.background, egui::Checkbox::new(&mut self.opts.guard, "pause when game not in front"));
                        });
                        ui.collapsing("process name / visible windows", |ui| {
                            ui.horizontal(|ui| {
                                ui.label("process contains:");
                                let mut p = self.opts.process.clone().unwrap_or_default();
                                if ui.text_edit_singleline(&mut p).changed() {
                                    self.opts.process = if p.trim().is_empty() { None } else { Some(p) };
                                }
                                ui.label("(empty = wwm.exe / yysls.exe)");
                            });
                            if ui.button("list windows").clicked() {
                                self.refresh_game();
                                self.show_windows = true;
                            }
                            if self.show_windows {
                                egui::ScrollArea::vertical().max_height(140.0).show(ui, |ui| {
                                    for (proc_name, title, area) in &self.windows {
                                        ui.monospace(format!("{:<24} {:>9}  {}", proc_name, area, title));
                                    }
                                });
                            }
                        });
                    });

                    ui.add_space(6.0);
                    ui.group(|ui| {
                        ui.set_min_width(full);
                        ui.label(egui::RichText::new("Timing").strong());
                        ui.add(egui::Slider::new(&mut self.opts.speed, 0.5..=1.5).text("speed").fixed_decimals(2));
                        ui.add(egui::Slider::new(&mut self.opts.modifier_settle_ms, 20..=100).text("Shift/Ctrl settle (ms)"))
                            .on_hover_text("50 passed the in-game calibration; raise it if sharps come out as naturals");
                        let mut hold = self.opts.hold_ms.unwrap_or(20);
                        if ui.add(egui::Slider::new(&mut hold, 10..=60).text("key hold (ms)")).changed() {
                            self.opts.hold_ms = Some(hold);
                        }
                        ui.horizontal(|ui| {
                            ui.add(egui::Slider::new(&mut self.opts.lead_in, 0.0..=10.0).text("lead-in (s)").fixed_decimals(0));
                            ui.add(egui::DragValue::new(&mut self.opts.start_at).range(0.0..=3600.0).speed(1.0).suffix(" s").prefix("start at "));
                        });
                    });

                    ui.add_space(6.0);
                    ui.group(|ui| {
                        ui.set_min_width(full);
                        ui.label(egui::RichText::new("Recording").strong());
                        ui.horizontal(|ui| {
                            ui.checkbox(&mut self.record, "record what the PC plays to");
                            ui.add_enabled(self.record, egui::TextEdit::singleline(&mut self.record_name).desired_width(180.0));
                        });
                        ui.add_enabled_ui(self.record, |ui| {
                            ui.horizontal(|ui| {
                                ui.label("device:");
                                let current = if self.device_idx == 0 {
                                    "default output".to_string()
                                } else {
                                    self.devices.get(self.device_idx - 1).cloned().unwrap_or_default()
                                };
                                egui::ComboBox::from_id_salt("device").width(260.0).selected_text(current).show_ui(ui, |ui| {
                                    ui.selectable_value(&mut self.device_idx, 0, "default output");
                                    for (i, d) in self.devices.iter().enumerate() {
                                        ui.selectable_value(&mut self.device_idx, i + 1, d);
                                    }
                                });
                                if ui.button("↻").clicked() {
                                    self.refresh_devices();
                                }
                            });
                        });
                        ui.label("the .wav and its .json sidecar go next to the exe, ready for `wwm verify`");
                    });
                });

                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    let can_start = self.selected.is_some() && self.info.is_some() && !playing;
                    if ui.add_enabled(can_start, egui::Button::new(egui::RichText::new("▶  Start").size(18.0)).min_size([120.0, 36.0].into())).clicked() {
                        self.start();
                    }
                    let pause_label = if self.paused { "▶  Resume" } else { "⏸  Pause" };
                    if ui.add_enabled(playing, egui::Button::new(egui::RichText::new(pause_label).size(18.0)).min_size([120.0, 36.0].into())).clicked() {
                        if let Some(run) = &self.running {
                            run.control.request_toggle();
                        }
                    }
                    if ui.add_enabled(playing, egui::Button::new(egui::RichText::new("■  Stop").size(18.0)).min_size([120.0, 36.0].into())).clicked() {
                        if let Some(run) = &self.running {
                            run.control.request_stop();
                        }
                    }
                });
                ui.label(egui::RichText::new("F8 starts or pauses, F9 or Esc stops, from any window").weak());
                ui.add_space(6.0);
                let secs = self.position_s as u64;
                ui.add(egui::ProgressBar::new(self.progress).text(format!("{:02}:{:02}  {}", secs / 60, secs % 60, self.notes)));
                if let Some(e) = &self.last_error {
                    ui.colored_label(egui::Color32::from_rgb(220, 80, 60), e);
                }
                ui.add_space(6.0);
                ui.group(|ui| {
                    ui.set_min_width(full);
                    ui.label(egui::RichText::new("Log").strong());
                    if self.log.is_empty() {
                        ui.label(egui::RichText::new("nothing yet").weak());
                    }
                    for line in &self.log {
                        ui.monospace(line);
                    }
                });
                ui.add_space(10.0);
                ui.horizontal(|ui| {
                    ui.label(egui::RichText::new(format!("Created by {CREATOR} · v{}", env!("CARGO_PKG_VERSION"))).weak());
                    ui.hyperlink_to(egui::RichText::new("github.com/HandsomeWJ/wwm-konghou").weak(), REPO_URL);
                });
            });
        });
    }
}

pub fn run() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([560.0, 720.0]).with_min_inner_size([480.0, 560.0]).with_title("WWM Konghou Player"),
        ..Default::default()
    };
    eframe::run_native("WWM Konghou Player", options, Box::new(|cc| Ok(Box::new(App::new(cc)))))
}
