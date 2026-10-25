//! Turning recorded audio into text with a local Whisper model.

use std::path::Path;

use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

/// Whisper takes 16 kHz mono.
const WHISPER_SAMPLE_RATE: u32 = 16_000;

/// `input` at `source_rate` resampled to 16 kHz by linear interpolation.
pub fn to_whisper_rate(input: &[f32], source_rate: u32) -> Vec<f32> {
    if source_rate == WHISPER_SAMPLE_RATE {
        return input.to_vec();
    }
    let length = (input.len() as u64 * WHISPER_SAMPLE_RATE as u64 / source_rate as u64) as usize;
    let step = source_rate as f64 / WHISPER_SAMPLE_RATE as f64;
    let last = input.len().saturating_sub(1);
    (0..length)
        .map(|n| {
            let at = n as f64 * step;
            let low = at.floor() as usize;
            let high = (low + 1).min(last);
            let weight = (at - low as f64) as f32;
            input[low] * (1.0 - weight) + input[high] * weight
        })
        .collect()
}

/// The English transcript of 16 kHz mono `samples`.
pub fn transcribe(model_path: &Path, samples: &[f32]) -> Result<String, String> {
    if !model_path.is_file() {
        return Err("The local voice model is not installed yet.".to_owned());
    }
    let path = model_path
        .to_str()
        .ok_or_else(|| "The local voice model path is not valid text.".to_owned())?;
    let context = WhisperContext::new_with_params(path, WhisperContextParameters::default())
        .map_err(|e| format!("Could not load the local voice model: {e}"))?;
    let mut state = context
        .create_state()
        .map_err(|e| format!("Could not prepare transcription: {e}"))?;

    let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
    params.set_language(Some("en"));
    params.set_translate(false);
    params.set_print_progress(false);
    params.set_print_special(false);
    params.set_print_realtime(false);
    params.set_suppress_blank(true);
    state
        .full(params, samples)
        .map_err(|e| format!("Could not transcribe the recording: {e}"))?;

    let mut text = String::new();
    for segment in state.as_iter() {
        let piece = segment
            .to_str()
            .map_err(|e| format!("Could not read the transcript: {e}"))?;
        text.push_str(piece);
    }
    Ok(text.trim().to_owned())
}

#[cfg(test)]
mod tests {
    use super::to_whisper_rate;

    #[test]
    fn a_16khz_stream_passes_through_unchanged() {
        let input = vec![0.0, 0.5, -0.5];
        assert_eq!(to_whisper_rate(&input, 16_000), input);
    }

    #[test]
    fn resampling_scales_the_duration() {
        assert_eq!(to_whisper_rate(&vec![0.0; 48_000], 48_000).len(), 16_000);
        assert_eq!(to_whisper_rate(&vec![0.0; 8_000], 8_000).len(), 16_000);
    }

    #[test]
    fn an_upsampled_ramp_stays_between_its_neighbours() {
        let out = to_whisper_rate(&[0.0, 1.0], 8_000);
        assert_eq!(out.len(), 4);
        assert!(out.windows(2).all(|pair| pair[0] <= pair[1]));
    }
}
