//! `reeve-stt-host` speaks JSON lines: what the Electron shell sends and reads. No
//! microphone and no model is needed for any of these.

use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};

fn host(models: &std::path::Path) -> std::process::Child {
    Command::new(env!("CARGO_BIN_EXE_reeve-stt-host"))
        .args(["--models", models.to_str().unwrap()])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("the host starts")
}

fn ask(child: &mut std::process::Child, reader: &mut impl BufRead, line: &str) -> serde_json::Value {
    let stdin = child.stdin.as_mut().unwrap();
    writeln!(stdin, "{line}").unwrap();
    stdin.flush().unwrap();
    let mut reply = String::new();
    reader.read_line(&mut reply).unwrap();
    serde_json::from_str(&reply).expect("a JSON reply")
}

#[test]
fn it_answers_by_id_and_says_what_it_cannot_do() {
    let models = std::env::temp_dir().join(format!("reeve-stt-host-test-{}", std::process::id()));
    let mut child = host(&models);
    let mut reader = BufReader::new(child.stdout.take().unwrap());

    let status = ask(&mut child, &mut reader, r#"{"id": 1, "cmd": "status"}"#);
    assert_eq!(status["id"], 1);
    assert_eq!(status["ok"], true);
    for field in [
        "recording", "model_installed", "model_verified", "test_passed", "download_in_progress",
        "model_name", "model_bytes", "supported", "device_summary", "compatibility_reason",
    ] {
        assert!(status["value"].get(field).is_some(), "status lacks {field}");
    }
    assert_eq!(status["value"]["recording"], false);
    assert_eq!(status["value"]["model_installed"], false);

    let level = ask(&mut child, &mut reader, r#"{"id": 2, "cmd": "level"}"#);
    assert_eq!((level["id"].as_u64(), level["ok"].as_bool(), level["value"].as_f64()), (Some(2), Some(true), Some(0.0)));

    let unknown = ask(&mut child, &mut reader, r#"{"id": 3, "cmd": "explode"}"#);
    assert_eq!(unknown["ok"], false);
    assert_eq!(unknown["error"], "unknown command: explode");

    let garbled = ask(&mut child, &mut reader, "not json");
    assert_eq!(garbled["ok"], false);
    assert!(garbled["error"].as_str().unwrap().starts_with("not a request"));

    // A stop with nothing recorded is an error, not a hang.
    let stop = ask(&mut child, &mut reader, r#"{"id": 4, "cmd": "stop"}"#);
    assert_eq!(stop["id"], 4);
    assert_eq!(stop["ok"], false);

    // The shell going closes stdin, and the host ends with it.
    drop(child.stdin.take());
    let status = child.wait().expect("the host ends");
    assert!(status.success());
    let _ = std::fs::remove_dir_all(models);
}
