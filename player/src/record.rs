//! Records what the PC is playing (WASAPI loopback on an output device) while a
//! script runs, so a calibration can be checked without anyone listening.
#![cfg(windows)]

use std::sync::{Arc, Mutex};
use std::time::Instant;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Sample, SizedSample};

pub struct Recorder {
    _capture: cpal::Stream,
    _keepalive: cpal::Stream,
    samples: Arc<Mutex<Vec<f32>>>,
    pub sample_rate: u32,
    pub channels: u16,
    pub device_name: String,
    started: Instant,
}

fn pick_device(filter: Option<&str>) -> Result<cpal::Device, String> {
    let host = cpal::default_host();
    match filter {
        Some(f) => {
            let needle = f.to_lowercase();
            host.output_devices()
                .map_err(|e| format!("cannot list output devices: {e}"))?
                .find(|d| d.name().unwrap_or_default().to_lowercase().contains(&needle))
                .ok_or_else(|| format!("no output device name contains {f:?} (see --list-devices)"))
        }
        None => host.default_output_device().ok_or_else(|| "no default output device".to_string()),
    }
}

pub fn list_devices() -> Vec<String> {
    let host = cpal::default_host();
    let default = host.default_output_device().and_then(|d| d.name().ok()).unwrap_or_default();
    host.output_devices()
        .map(|it| {
            it.map(|d| {
                let name = d.name().unwrap_or_default();
                if name == default {
                    format!("{name}  (default)")
                } else {
                    name
                }
            })
            .collect()
        })
        .unwrap_or_default()
}

fn build_capture<T>(device: &cpal::Device, config: &cpal::StreamConfig, sink: Arc<Mutex<Vec<f32>>>) -> Result<cpal::Stream, String>
where
    T: SizedSample,
    f32: cpal::FromSample<T>,
{
    device
        .build_input_stream(
            config,
            move |data: &[T], _| {
                let mut buf = sink.lock().unwrap();
                buf.extend(data.iter().map(|s| f32::from_sample(*s)));
            },
            |e| eprintln!("capture error: {e}"),
            None,
        )
        .map_err(|e| format!("cannot open loopback capture: {e}"))
}

impl Recorder {
    /// Start capturing the given (or default) output device. A silent output stream on
    /// the same device keeps the audio engine running, so the capture never pauses
    /// during silence and the timeline stays continuous.
    pub fn start(filter: Option<&str>) -> Result<Recorder, String> {
        let device = pick_device(filter)?;
        let device_name = device.name().unwrap_or_default();
        let supported = device.default_output_config().map_err(|e| format!("no output config: {e}"))?;
        let config: cpal::StreamConfig = supported.clone().into();
        let samples = Arc::new(Mutex::new(Vec::<f32>::new()));
        let capture = match supported.sample_format() {
            cpal::SampleFormat::F32 => build_capture::<f32>(&device, &config, samples.clone())?,
            cpal::SampleFormat::I16 => build_capture::<i16>(&device, &config, samples.clone())?,
            cpal::SampleFormat::U16 => build_capture::<u16>(&device, &config, samples.clone())?,
            cpal::SampleFormat::I32 => build_capture::<i32>(&device, &config, samples.clone())?,
            other => return Err(format!("unsupported sample format {other:?}")),
        };
        let keepalive = device
            .build_output_stream(
                &config,
                move |data: &mut [f32], _| data.iter_mut().for_each(|s| *s = 0.0),
                |e| eprintln!("keepalive error: {e}"),
                None,
            )
            .map_err(|e| format!("cannot open keepalive output: {e}"))?;
        keepalive.play().map_err(|e| format!("keepalive: {e}"))?;
        capture.play().map_err(|e| format!("capture: {e}"))?;
        Ok(Recorder {
            _capture: capture,
            _keepalive: keepalive,
            samples,
            sample_rate: config.sample_rate.0,
            channels: config.channels,
            device_name,
            started: Instant::now(),
        })
    }

    /// Stop, downmix to mono and write a 16-bit WAV. Returns (seconds recorded, seconds elapsed).
    pub fn finish(self, path: &str) -> Result<(f64, f64), String> {
        let elapsed = self.started.elapsed().as_secs_f64();
        drop(self._capture);
        drop(self._keepalive);
        let data = self.samples.lock().unwrap();
        let ch = self.channels.max(1) as usize;
        let frames = data.len() / ch;
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: self.sample_rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::create(path, spec).map_err(|e| format!("cannot write {path}: {e}"))?;
        for f in 0..frames {
            let mono: f32 = data[f * ch..(f + 1) * ch].iter().sum::<f32>() / ch as f32;
            writer
                .write_sample((mono.clamp(-1.0, 1.0) * 32767.0) as i16)
                .map_err(|e| format!("write error: {e}"))?;
        }
        writer.finalize().map_err(|e| format!("finalize error: {e}"))?;
        Ok((frames as f64 / self.sample_rate as f64, elapsed))
    }
}
