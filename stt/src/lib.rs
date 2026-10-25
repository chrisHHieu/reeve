//! A local, offline speech-to-text engine.
//!
//! The crate has no Tauri, UI, clipboard or global-shortcut dependency. A host owns its own UX and
//! permission flows and uses [`Dictation`] for microphone capture, model provisioning and the final
//! transcription.

mod capture;
mod model;
mod speech;

use std::sync::atomic::{AtomicBool, Ordering};

use serde::Serialize;

use capture::Capture;
pub use model::{DEFAULT_MODEL_BYTES, DEFAULT_MODEL_FILE, DEFAULT_MODEL_SHA256, DEFAULT_MODEL_URL};
use model::ModelFiles;

#[derive(Debug, Clone, Serialize)]
pub struct DictationStatus {
    pub recording: bool,
    pub model_installed: bool,
    pub model_verified: bool,
    pub test_passed: bool,
    pub download_in_progress: bool,
    pub model_name: &'static str,
    pub model_bytes: u64,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct DownloadProgress {
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
}

/// A reusable single-microphone dictation session manager.
///
/// It records only while a host has explicitly started a session. The audio is held in memory for
/// that session and never written to disk, and the downloaded recognition model is the only data
/// kept under `model_dir`.
pub struct Dictation {
    files: ModelFiles,
    capture: Capture,
    download_in_progress: AtomicBool,
    cancel_download: AtomicBool,
}

/// A recording shorter than a quarter of a second is treated as no speech at all.
const MIN_RECORDING_FRACTION: usize = 4;

impl Dictation {
    pub fn new(model_dir: impl Into<std::path::PathBuf>) -> Self {
        Self {
            files: ModelFiles::in_dir(model_dir),
            capture: Capture::spawn(),
            download_in_progress: AtomicBool::new(false),
            cancel_download: AtomicBool::new(false),
        }
    }

    pub fn status(&self) -> DictationStatus {
        let model_installed = self.files.is_installed();
        DictationStatus {
            recording: self.capture.is_recording(),
            model_installed,
            model_verified: model_installed && self.files.is_verified(),
            test_passed: self.files.test_passed(),
            download_in_progress: self.download_in_progress.load(Ordering::SeqCst),
            model_name: "Whisper Base English (local)",
            model_bytes: DEFAULT_MODEL_BYTES,
        }
    }

    pub fn model_path(&self) -> &std::path::Path {
        self.files.model()
    }

    /// Downloads the default model atomically. A host should call this only after an explicit user
    /// action, because it is a sizeable download.
    pub fn install_default_model(&self) -> Result<(), String> {
        self.install_default_model_with_progress(|_| {})
    }

    /// Downloads and verifies the default model atomically, reporting byte progress to the host.
    /// A canceled or failed transfer never replaces a model that was already verified.
    pub fn install_default_model_with_progress(
        &self,
        on_progress: impl FnMut(DownloadProgress),
    ) -> Result<(), String> {
        if self.status().model_verified {
            return Ok(());
        }
        if self
            .download_in_progress
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return Err("The local voice model is already downloading.".to_owned());
        }
        self.cancel_download.store(false, Ordering::SeqCst);
        let result = self.files.download(&self.cancel_download, on_progress);
        self.download_in_progress.store(false, Ordering::SeqCst);
        self.cancel_download.store(false, Ordering::SeqCst);
        result
    }

    /// Verifies an already-installed model, including one installed by an older app version.
    pub fn verify_default_model(&self) -> Result<(), String> {
        self.files.verify_installed()
    }

    pub fn cancel_model_download(&self) {
        self.cancel_download.store(true, Ordering::SeqCst);
    }

    pub fn mark_test_passed(&self) -> Result<(), String> {
        if !self.status().model_verified {
            return Err("Verify the local voice model before testing it.".to_owned());
        }
        self.files.record_test_passed()
    }

    pub fn delete_default_model(&self) -> Result<(), String> {
        self.cancel_model_download();
        self.cancel();
        self.files.remove_all()
    }

    /// Begins microphone capture. A host must call
    /// [`stop_and_transcribe`](Self::stop_and_transcribe) or [`cancel`](Self::cancel) before a new
    /// recording can start.
    pub fn start(&self) -> Result<(), String> {
        if !self.status().model_verified {
            return Err("Set up and verify Voice Input in Settings first.".to_owned());
        }
        self.capture.start()
    }

    /// Stops capture and returns the final local transcript. It is synchronous on purpose, so a host
    /// can run it off its UI thread and decide how to present completion and error states.
    pub fn stop_and_transcribe(&self) -> Result<String, String> {
        let audio = self.capture.stop()?;
        if audio.samples.len() < audio.sample_rate as usize / MIN_RECORDING_FRACTION {
            return Ok(String::new());
        }
        speech::transcribe(self.files.model(), &speech::to_whisper_rate(&audio.samples, audio.sample_rate))
    }

    /// Input loudness of the recording in progress, 0.0..=1.0, and 0.0 while not recording.
    pub fn input_level(&self) -> f32 {
        self.capture.level()
    }

    /// Discards the recording in progress without keeping or transcribing it.
    pub fn cancel(&self) {
        self.capture.cancel();
    }
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    use super::{Dictation, DEFAULT_MODEL_BYTES, DEFAULT_MODEL_FILE};

    #[test]
    fn readiness_requires_a_verified_model_and_persists_after_a_test() {
        let unique = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir().join(format!("reeve-stt-readiness-{unique}"));
        fs::create_dir_all(&dir).unwrap();
        let model = dir.join(DEFAULT_MODEL_FILE);
        fs::File::create(&model).unwrap().set_len(DEFAULT_MODEL_BYTES).unwrap();

        let dictation = Dictation::new(&dir);
        assert!(!dictation.status().model_verified);
        assert!(dictation.mark_test_passed().is_err());
        assert!(dictation.start().is_err());

        // Record that the file passed verification the way an install does.
        fs::write(
            model.with_extension("bin.verified"),
            format!(
                "{}\n{}\n",
                super::DEFAULT_MODEL_SHA256,
                fs::metadata(&model)
                    .unwrap()
                    .modified()
                    .unwrap()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_millis()
            ),
        )
        .unwrap();
        assert!(dictation.status().model_verified);
        assert!(!dictation.status().test_passed);
        dictation.mark_test_passed().unwrap();
        assert!(dictation.status().test_passed);
        dictation.delete_default_model().unwrap();
        assert!(!dictation.status().model_installed);
        drop(dictation);
        fs::remove_dir_all(dir).unwrap();
    }
}
