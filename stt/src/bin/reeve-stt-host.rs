//! `reeve-stt-host`: the dictation engine as a program of its own.
//!
//! The Electron shell cannot link this crate, so it starts this program on the first use of Voice
//! Input and speaks to it in JSON lines on stdin/stdout:
//!
//! - a request `{"id": 1, "cmd": "status"}` is answered `{"id": 1, "ok": true, "value": …}` or
//!   `{"id": 1, "ok": false, "error": "…"}`;
//! - a model download reports `{"event": "progress", "downloaded_bytes": …, "total_bytes": …}`.
//!
//! Every request runs on a thread of its own, so `level` and `cancel` are answered while a
//! recording or a download is going. Whether this machine can run Voice Input (the shell's check
//! in the Tauri build) is said here, beside the engine it is about.
//!
//! `--models <dir>` names where the model is kept. The program ends when its stdin closes, that
//! is, when the shell goes.

use std::io::{BufRead, Write};
use std::sync::{Arc, Mutex};

use reeve_stt::{Dictation, DownloadProgress};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
struct Request {
    id: u64,
    cmd: String,
}

type Out = Arc<Mutex<std::io::Stdout>>;

fn send(out: &Out, message: Value) {
    let mut out = out.lock().unwrap();
    let _ = writeln!(out, "{message}");
    let _ = out.flush();
}

fn status(dictation: &Dictation) -> Value {
    let status = dictation.status();
    let (supported, device_summary, compatibility_reason) = compatibility();
    json!({
        "recording": status.recording,
        "model_installed": status.model_installed,
        "model_verified": status.model_verified,
        "test_passed": status.test_passed,
        "download_in_progress": status.download_in_progress,
        "model_name": status.model_name,
        "model_bytes": status.model_bytes,
        "supported": supported,
        "device_summary": device_summary,
        "compatibility_reason": compatibility_reason,
    })
}

#[cfg(target_os = "macos")]
fn compatibility() -> (bool, String, Option<String>) {
    let version = std::process::Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_else(|| "unknown version".to_owned());
    let major = version.split('.').next().and_then(|part| part.parse::<u32>().ok()).unwrap_or(0);
    let apple_silicon = std::env::consts::ARCH == "aarch64";
    let summary = format!("macOS {version} · {}", if apple_silicon { "Apple Silicon" } else { "Intel" });
    let reason = if !apple_silicon {
        Some("Voice Input currently requires an Apple Silicon Mac (M1 or newer).".to_owned())
    } else if major < 12 {
        Some("Voice Input requires macOS 12 or newer.".to_owned())
    } else {
        None
    };
    (reason.is_none(), summary, reason)
}

#[cfg(target_os = "windows")]
fn compatibility() -> (bool, String, Option<String>) {
    use std::os::windows::process::CommandExt;
    let version = std::process::Command::new("cmd")
        .args(["/C", "ver"])
        .creation_flags(0x0800_0000) // no console window
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_else(|| "Windows (unknown version)".to_owned());
    let build = version
        .split(|character: char| !character.is_ascii_digit() && character != '.')
        .find(|part| part.matches('.').count() >= 2)
        .and_then(|part| part.split('.').nth(2))
        .and_then(|part| part.parse::<u32>().ok())
        .unwrap_or(0);
    let x64 = std::env::consts::ARCH == "x86_64";
    let reason = if !x64 {
        Some("Voice Input currently requires a 64-bit x64 Windows PC.".to_owned())
    } else if build < 19_045 {
        Some("Voice Input requires Windows 10 22H2 or Windows 11.".to_owned())
    } else {
        None
    };
    (reason.is_none(), format!("{version} · x64"), reason)
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn compatibility() -> (bool, String, Option<String>) {
    (
        false,
        format!("{} · {}", std::env::consts::OS, std::env::consts::ARCH),
        Some("Voice Input is currently supported on macOS and Windows.".to_owned()),
    )
}

fn answer(dictation: &Dictation, cmd: &str, out: &Out) -> Result<Value, String> {
    match cmd {
        "status" => Ok(status(dictation)),
        "start" => {
            let (supported, _, reason) = compatibility();
            if !supported {
                return Err(reason.unwrap_or_else(|| "Voice Input is not supported on this device.".to_owned()));
            }
            dictation.start()?;
            Ok(status(dictation))
        }
        "stop" => dictation.stop_and_transcribe().map(Value::String),
        "cancel" => {
            dictation.cancel();
            Ok(Value::Null)
        }
        "level" => Ok(json!(dictation.input_level())),
        "download" => {
            let progress_out = out.clone();
            dictation.install_default_model_with_progress(move |progress: DownloadProgress| {
                send(
                    &progress_out,
                    json!({ "event": "progress", "downloaded_bytes": progress.downloaded_bytes, "total_bytes": progress.total_bytes }),
                );
            })?;
            Ok(status(dictation))
        }
        "cancel_download" => {
            dictation.cancel_model_download();
            Ok(Value::Null)
        }
        "verify" => {
            dictation.verify_default_model()?;
            Ok(status(dictation))
        }
        "mark_test_passed" => {
            dictation.mark_test_passed()?;
            Ok(status(dictation))
        }
        "delete" => {
            dictation.delete_default_model()?;
            Ok(status(dictation))
        }
        other => Err(format!("unknown command: {other}")),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let models = args
        .iter()
        .position(|arg| arg == "--models")
        .and_then(|at| args.get(at + 1))
        .cloned()
        .unwrap_or_else(|| "models".to_owned());
    let dictation = Arc::new(Dictation::new(models));
    let out: Out = Arc::new(Mutex::new(std::io::stdout()));
    let stdin = std::io::stdin();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let request: Request = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(error) => {
                send(&out, json!({ "ok": false, "error": format!("not a request: {error}") }));
                continue;
            }
        };
        let dictation = dictation.clone();
        let out = out.clone();
        std::thread::spawn(move || {
            let reply = match answer(&dictation, &request.cmd, &out) {
                Ok(value) => json!({ "id": request.id, "ok": true, "value": value }),
                Err(error) => json!({ "id": request.id, "ok": false, "error": error }),
            };
            send(&out, reply);
        });
    }
}
