// SPDX-License-Identifier: Unlicense
//! The app's core: it starts the engine (`python -m overpressure serve --json`) and passes lines both ways,
//! nothing else. The web view has no shell or file access of its own: its one command is `engine_send`, each
//! line the engine writes arrives as an "engine-line" event, and the system's file dialogs hand it the paths
//! the owner picks (the backup's export and import, the logs, a step's paths). Closing the window closes the engine's
//! stdin, which stops a run under way (after a write, never in one) and ends the engine.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

/// The engine's stdin, and the process to wait for when the app ends.
struct Engine {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
}

/// Where `python -m overpressure` runs: OVERPRESSURE_ROOT, else the checkout this core was built in.
/// The bundled app's own root (its Python and overpressure inside it) replaces this with the packaging.
fn root() -> PathBuf {
    std::env::var_os("OVERPRESSURE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("..")
                .join("..")
        })
}

/// The engine's command line. The demo unless OVERPRESSURE_APP_REAL=1: until the app is trusted, a real run
/// is something a developer asks for.
fn command() -> Command {
    let python = std::env::var("OVERPRESSURE_PYTHON").unwrap_or_else(|_| "python".into());
    let mut command = Command::new(python);
    command.args(["-m", "overpressure", "serve", "--json"]);
    if std::env::var("OVERPRESSURE_APP_REAL").as_deref() != Ok("1") {
        command.arg("--demo");
    }
    command
        .current_dir(root())
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000; // no console window of its own
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

/// Start the engine and pass each line of its stdout to the web view; its stderr goes to this process's.
fn start(app: &AppHandle) -> std::io::Result<Engine> {
    let mut child = command().spawn()?;
    let stdout = child.stdout.take().expect("piped stdout");
    let stderr = child.stderr.take().expect("piped stderr");
    let handle = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Err(error) = handle.emit("engine-line", line) {
                eprintln!("engine-line: {error}");
            }
        }
        let _ = handle.emit("engine-line", r#"{"event": "engine-ended", "run": ""}"#);
    });
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            eprintln!("engine: {line}");
        }
    });
    Ok(Engine {
        stdin: Mutex::new(child.stdin.take()),
        child: Mutex::new(Some(child)),
    })
}

/// Write one request line (JSON, as the front end made it) to the engine.
#[tauri::command]
fn engine_send(line: String, engine: State<'_, Engine>) -> Result<(), String> {
    if line.contains('\n') {
        return Err("one line at a time".into());
    }
    let mut stdin = engine.stdin.lock().map_err(|e| e.to_string())?;
    let pipe = stdin.as_mut().ok_or("the engine has ended")?;
    writeln!(pipe, "{line}")
        .and_then(|()| pipe.flush())
        .map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let engine = start(app.handle())?;
            app.manage(engine);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_send])
        .build(tauri::generate_context!())
        .expect("the app could not start")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let engine = app.state::<Engine>();
                // A closed stdin stops a run under way and ends the engine; wait for it.
                if let Ok(mut stdin) = engine.stdin.lock() {
                    stdin.take();
                }
                let child = engine.child.lock().ok().and_then(|mut c| c.take());
                if let Some(mut child) = child {
                    let _ = child.wait();
                }
            }
        });
}
