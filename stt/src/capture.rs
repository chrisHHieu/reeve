//! Microphone capture on one dedicated thread.
//!
//! CPAL's CoreAudio stream is deliberately `!Send`. It stays on a single owner thread, and the rest
//! of the program talks to that thread over a channel, instead of forcing the stream through
//! Tauri's `Send + Sync` application state with unsafe code.

use std::{
    sync::{
        mpsc::{self, Receiver, Sender},
        Arc, Mutex,
    },
    thread,
};

use cpal::{
    traits::{DeviceTrait, HostTrait, StreamTrait},
    SampleFormat, Stream, StreamConfig,
};

const WORKER_GONE: &str = "Dictation is unavailable because its audio worker stopped.";

/// Mono samples at the device's own rate.
pub struct RecordedAudio {
    pub samples: Vec<f32>,
    pub sample_rate: u32,
}

type SharedSamples = Arc<Mutex<Vec<f32>>>;
/// The in-flight recording's sample buffer and rate, visible while a session runs so a host can
/// meter input loudness.
type LiveBuffer = Arc<Mutex<Option<(SharedSamples, u32)>>>;

struct Recording {
    stream: Stream,
    samples: SharedSamples,
    sample_rate: u32,
}

enum Command {
    Start(Sender<Result<(), String>>),
    Stop(Sender<Result<RecordedAudio, String>>),
    Cancel(Sender<()>),
}

/// The handle to the capture thread.
pub struct Capture {
    commands: Sender<Command>,
    recording: Arc<Mutex<bool>>,
    live: LiveBuffer,
}

impl Capture {
    pub fn spawn() -> Self {
        let (commands, receiver) = mpsc::channel();
        let recording = Arc::new(Mutex::new(false));
        let live: LiveBuffer = Arc::new(Mutex::new(None));
        let (worker_recording, worker_live) = (recording.clone(), live.clone());
        thread::spawn(move || run_worker(receiver, worker_recording, worker_live));
        Self { commands, recording, live }
    }

    pub fn is_recording(&self) -> bool {
        self.recording.lock().map(|active| *active).unwrap_or(false)
    }

    fn ask<T>(&self, build: impl FnOnce(Sender<T>) -> Command) -> Result<T, String> {
        let (reply, answer) = mpsc::channel();
        self.commands.send(build(reply)).map_err(|_| WORKER_GONE.to_owned())?;
        answer.recv().map_err(|_| WORKER_GONE.to_owned())
    }

    pub fn start(&self) -> Result<(), String> {
        self.ask(Command::Start)?
    }

    pub fn stop(&self) -> Result<RecordedAudio, String> {
        self.ask(Command::Stop)?
    }

    /// Discards the recording in progress, if any.
    pub fn cancel(&self) {
        let _ = self.ask(Command::Cancel);
    }

    /// RMS over the most recent ~100 ms, scaled so conversational speech spans most of 0.0..=1.0;
    /// 0.0 while not recording. Cheap enough to poll at UI frame rates.
    pub fn level(&self) -> f32 {
        let Ok(live) = self.live.lock() else {
            return 0.0;
        };
        let Some((samples, sample_rate)) = live.as_ref() else {
            return 0.0;
        };
        let Ok(samples) = samples.lock() else {
            return 0.0;
        };
        let window = (*sample_rate as usize / 10).max(1);
        let tail = &samples[samples.len().saturating_sub(window)..];
        if tail.is_empty() {
            return 0.0;
        }
        let mean_square = tail.iter().map(|s| s * s).sum::<f32>() / tail.len() as f32;
        (mean_square.sqrt() * 8.0).clamp(0.0, 1.0)
    }
}

fn run_worker(receiver: Receiver<Command>, recording_flag: Arc<Mutex<bool>>, live: LiveBuffer) {
    let mut current: Option<Recording> = None;
    let publish = |value: Option<(SharedSamples, u32)>| {
        if let Ok(mut guard) = live.lock() {
            *guard = value;
        }
    };
    let set_flag = |value: bool| {
        if let Ok(mut flag) = recording_flag.lock() {
            *flag = value;
        }
    };
    for command in receiver {
        match command {
            Command::Start(reply) => {
                if current.is_some() {
                    let _ = reply.send(Err("Dictation is already recording.".to_owned()));
                    continue;
                }
                let outcome = open_microphone().map(|next| {
                    publish(Some((next.samples.clone(), next.sample_rate)));
                    current = Some(next);
                    set_flag(true);
                });
                let _ = reply.send(outcome);
            }
            Command::Stop(reply) => {
                publish(None);
                let audio = current
                    .take()
                    .ok_or_else(|| "Dictation is not recording.".to_owned())
                    .and_then(close_microphone);
                set_flag(false);
                let _ = reply.send(audio);
            }
            Command::Cancel(reply) => {
                publish(None);
                current.take();
                set_flag(false);
                let _ = reply.send(());
            }
        }
    }
}

fn open_microphone() -> Result<Recording, String> {
    let device = cpal::default_host()
        .default_input_device()
        .ok_or_else(|| "No microphone is available. Check your Mac sound settings.".to_owned())?;
    let supported = device
        .default_input_config()
        .map_err(|e| format!("Could not open the microphone: {e}"))?;
    let config: StreamConfig = supported.clone().into();
    let samples: SharedSamples = Arc::new(Mutex::new(Vec::new()));
    let stream = build_stream(&device, &config, supported.sample_format(), samples.clone())?;
    stream
        .play()
        .map_err(|e| format!("Could not start microphone recording: {e}"))?;
    Ok(Recording { stream, samples, sample_rate: config.sample_rate.0 })
}

fn close_microphone(recording: Recording) -> Result<RecordedAudio, String> {
    let Recording { stream, samples, sample_rate } = recording;
    drop(stream);
    let samples = samples
        .lock()
        .map_err(|_| "Could not read the recorded audio.".to_owned())?
        .clone();
    Ok(RecordedAudio { samples, sample_rate })
}

fn build_stream(
    device: &cpal::Device,
    config: &StreamConfig,
    format: SampleFormat,
    samples: SharedSamples,
) -> Result<Stream, String> {
    let channels = config.channels as usize;
    let on_error = |error| eprintln!("[reeve-stt] microphone stream error: {error}");
    let failed = |e| format!("Could not create microphone stream: {e}");
    match format {
        SampleFormat::F32 => device
            .build_input_stream(
                config,
                move |data: &[f32], _| mix_down(&samples, data, channels, |sample| sample),
                on_error,
                None,
            )
            .map_err(failed),
        SampleFormat::I16 => device
            .build_input_stream(
                config,
                move |data: &[i16], _| {
                    mix_down(&samples, data, channels, |sample| sample as f32 / i16::MAX as f32)
                },
                on_error,
                None,
            )
            .map_err(failed),
        SampleFormat::U16 => device
            .build_input_stream(
                config,
                move |data: &[u16], _| {
                    mix_down(&samples, data, channels, |sample| {
                        (sample as f32 / u16::MAX as f32) * 2.0 - 1.0
                    })
                },
                on_error,
                None,
            )
            .map_err(failed),
        other => Err(format!("Unsupported microphone sample format: {other:?}")),
    }
}

/// Appends interleaved frames to `target` as mono, each frame averaged over its channels.
fn mix_down<T: Copy>(target: &SharedSamples, data: &[T], channels: usize, convert: impl Fn(T) -> f32) {
    let Ok(mut output) = target.lock() else {
        return;
    };
    let width = channels.max(1);
    output.reserve(data.len() / width);
    for frame in data.chunks(width) {
        let sum: f32 = frame.iter().copied().map(&convert).sum();
        output.push(sum / frame.len() as f32);
    }
}
