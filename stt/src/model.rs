//! The recognition model on disk: where it lives, whether it can be trusted, and how it arrives.
//!
//! Four files sit beside each other under the model directory: the model itself, a `.part` file
//! while a download is running, a `.verified` marker that records the checksum and modification
//! time of a model that passed verification, and a `.ready` marker left by a successful voice test.

use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, UNIX_EPOCH},
};

use sha2::{Digest, Sha256};

use crate::DownloadProgress;

/// A reasonably fast English model for short prompts (~142 MB).
pub const DEFAULT_MODEL_FILE: &str = "ggml-base.en.bin";
pub const DEFAULT_MODEL_URL: &str =
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin";
pub const DEFAULT_MODEL_BYTES: u64 = 147_964_211;
pub const DEFAULT_MODEL_SHA256: &str =
    "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002";

/// Progress is reported at most this often, in bytes, apart from the first and last report.
const REPORT_EVERY_BYTES: u64 = 512 * 1024;

/// The paths belonging to the one model this crate installs.
pub struct ModelFiles {
    model: PathBuf,
    partial: PathBuf,
    verified: PathBuf,
    ready: PathBuf,
}

impl ModelFiles {
    pub fn in_dir(dir: impl Into<PathBuf>) -> Self {
        let model = dir.into().join(DEFAULT_MODEL_FILE);
        Self {
            partial: model.with_extension("bin.part"),
            verified: model.with_extension("bin.verified"),
            ready: model.with_extension("bin.ready"),
            model,
        }
    }

    pub fn model(&self) -> &Path {
        &self.model
    }

    pub fn is_installed(&self) -> bool {
        self.model.is_file()
    }

    /// True when the installed file is the right size and its marker matches what is on disk.
    pub fn is_verified(&self) -> bool {
        let Ok(metadata) = fs::metadata(&self.model) else {
            return false;
        };
        if metadata.len() != DEFAULT_MODEL_BYTES {
            return false;
        }
        let Ok(marker) = fs::read_to_string(&self.verified) else {
            return false;
        };
        let mut lines = marker.lines();
        let same_hash = lines.next() == Some(DEFAULT_MODEL_SHA256);
        let recorded = lines.next().and_then(|value| value.parse::<u128>().ok());
        same_hash && recorded == modified_millis(&self.model)
    }

    pub fn test_passed(&self) -> bool {
        self.is_verified() && self.ready.is_file()
    }

    /// Checks the installed model against its size and checksum, and records that it passed.
    pub fn verify_installed(&self) -> Result<(), String> {
        check_file(&self.model)?;
        self.record_verified()
    }

    pub fn record_verified(&self) -> Result<(), String> {
        let modified = modified_millis(&self.model)
            .ok_or_else(|| "Could not read the installed voice model timestamp.".to_owned())?;
        fs::write(&self.verified, format!("{DEFAULT_MODEL_SHA256}\n{modified}\n"))
            .map_err(|e| format!("Could not record voice model verification: {e}"))
    }

    pub fn record_test_passed(&self) -> Result<(), String> {
        fs::write(&self.ready, b"ready")
            .map_err(|e| format!("Could not save the voice input test result: {e}"))
    }

    /// Removes the model, any partial download and both markers.
    pub fn remove_all(&self) -> Result<(), String> {
        for path in [&self.model, &self.partial, &self.verified, &self.ready] {
            if path.exists() {
                fs::remove_file(path)
                    .map_err(|e| format!("Could not remove {}: {e}", path.display()))?;
            }
        }
        Ok(())
    }

    /// Downloads the model to the `.part` file, checks it, and only then puts it in place, so a
    /// canceled or failed transfer never replaces a model that was already verified.
    pub fn download(
        &self,
        cancel: &AtomicBool,
        mut on_progress: impl FnMut(DownloadProgress),
    ) -> Result<(), String> {
        let parent = self
            .model
            .parent()
            .ok_or_else(|| "Could not determine the local model directory.".to_owned())?;
        fs::create_dir_all(parent).map_err(|e| format!("Could not create model directory: {e}"))?;

        // A read timeout and not an overall one: a 142 MB transfer legitimately takes minutes, but a
        // stalled connection must surface as an error. The cancel flag is only seen between reads,
        // so a read that blocked forever would make Cancel unresponsive too.
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(30))
            .timeout_read(Duration::from_secs(30))
            .build();
        let response = agent
            .get(DEFAULT_MODEL_URL)
            .call()
            .map_err(|e| format!("Could not download the local voice model: {e}"))?;
        let mut input = response.into_reader();
        let mut output = fs::File::create(&self.partial)
            .map_err(|e| format!("Could not save the local voice model: {e}"))?;

        let report = |downloaded_bytes| DownloadProgress {
            downloaded_bytes,
            total_bytes: DEFAULT_MODEL_BYTES,
        };
        let mut downloaded = 0_u64;
        let mut last_reported = 0_u64;
        let mut chunk = [0_u8; 64 * 1024];
        on_progress(report(0));
        loop {
            if cancel.load(Ordering::SeqCst) {
                drop(output);
                let _ = fs::remove_file(&self.partial);
                return Err("Voice model download canceled.".to_owned());
            }
            let count = input
                .read(&mut chunk)
                .map_err(|e| format!("Could not download the local voice model: {e}"))?;
            if count == 0 {
                break;
            }
            output
                .write_all(&chunk[..count])
                .map_err(|e| format!("Could not save the local voice model: {e}"))?;
            downloaded += count as u64;
            if downloaded - last_reported >= REPORT_EVERY_BYTES || downloaded == DEFAULT_MODEL_BYTES {
                last_reported = downloaded;
                on_progress(report(downloaded));
            }
        }
        output
            .flush()
            .map_err(|e| format!("Could not finish saving the local voice model: {e}"))?;
        drop(output);

        check_file(&self.partial)?;
        if self.model.exists() {
            fs::remove_file(&self.model)
                .map_err(|e| format!("Could not replace the local voice model: {e}"))?;
        }
        fs::rename(&self.partial, &self.model)
            .map_err(|e| format!("Could not install the local voice model: {e}"))?;
        self.record_verified()?;
        let _ = fs::remove_file(&self.ready);
        on_progress(report(DEFAULT_MODEL_BYTES));
        Ok(())
    }
}

fn modified_millis(path: &Path) -> Option<u128> {
    let modified = fs::metadata(path).ok()?.modified().ok()?;
    modified.duration_since(UNIX_EPOCH).ok().map(|since| since.as_millis())
}

/// The file has the published size and checksum.
fn check_file(path: &Path) -> Result<(), String> {
    let size = fs::metadata(path)
        .map_err(|e| format!("Could not read the local voice model: {e}"))?
        .len();
    if size != DEFAULT_MODEL_BYTES {
        return Err(format!(
            "The local voice model is incomplete ({size} of {DEFAULT_MODEL_BYTES} bytes)."
        ));
    }
    let mut file =
        fs::File::open(path).map_err(|e| format!("Could not read the local voice model: {e}"))?;
    let mut hasher = Sha256::new();
    let mut chunk = [0_u8; 128 * 1024];
    loop {
        let count = file
            .read(&mut chunk)
            .map_err(|e| format!("Could not verify the local voice model: {e}"))?;
        if count == 0 {
            break;
        }
        hasher.update(&chunk[..count]);
    }
    if format!("{:x}", hasher.finalize()) != DEFAULT_MODEL_SHA256 {
        return Err(
            "The local voice model failed its checksum. Repair the download in Settings.".to_owned(),
        );
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    use super::{ModelFiles, DEFAULT_MODEL_BYTES};

    #[test]
    fn default_model_size_matches_the_published_base_english_artifact() {
        assert_eq!(DEFAULT_MODEL_BYTES, 147_964_211);
    }

    #[test]
    fn a_marker_vouches_only_for_the_file_it_was_written_for() {
        let unique = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let dir = std::env::temp_dir().join(format!("reeve-stt-model-{unique}"));
        fs::create_dir_all(&dir).unwrap();
        let files = ModelFiles::in_dir(&dir);
        fs::File::create(files.model()).unwrap().set_len(DEFAULT_MODEL_BYTES).unwrap();

        assert!(files.is_installed());
        assert!(!files.is_verified());
        files.record_verified().unwrap();
        assert!(files.is_verified());
        assert!(!files.test_passed());
        files.record_test_passed().unwrap();
        assert!(files.test_passed());

        // A model of another size is never verified, whatever the marker says.
        fs::File::create(files.model()).unwrap().set_len(DEFAULT_MODEL_BYTES - 1).unwrap();
        assert!(!files.is_verified());

        files.remove_all().unwrap();
        assert!(!files.is_installed());
        fs::remove_dir_all(dir).unwrap();
    }
}
