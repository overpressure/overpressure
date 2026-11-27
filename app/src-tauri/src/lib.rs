// SPDX-License-Identifier: Unlicense
//! The app's core: it starts the engine (`python -m overpressure serve --json`) and passes lines both ways,
//! nothing else. The web view has no shell or file access of its own: its one command is `engine_send`, each
//! line the engine writes arrives as an "engine-line" event, and the system's file dialogs hand it the paths
//! the owner picks (the backup's export and import, the logs, a step's paths). Closing the window first asks the front
//! end ("close-requested"), which closes at once when nothing runs and asks the owner otherwise (`close_window`,
//! `keep_window`); a second close goes through whatever the page does. `install_update` replaces the app with a
//! newer release (update.rs). Closing closes the engine's stdin, which stops
//! a run under way (after a write, never in one) and ends the engine.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;

use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};

mod update;

/// The engine's stdin, and the process to wait for when the app ends.
struct Engine {
    stdin: Mutex<Option<ChildStdin>>,
    child: Mutex<Option<Child>>,
}

/// The engine the app was installed with (scripts/bundle-app.py, shipped by `pnpm bundle`): its own Python and
/// overpressure's root in the resources' `engine/`. None for a development build, which has neither.
fn bundled(app: &AppHandle) -> Option<(PathBuf, PathBuf)> {
    let dir = app.path().resource_dir().ok()?.join("engine");
    let python = if cfg!(windows) {
        dir.join("python").join("python.exe")
    } else {
        dir.join("python").join("bin").join("python3")
    };
    let root = dir.join("root");
    (python.is_file() && root.join("overpressure").is_dir()).then_some((python, root))
}

/// Which Python runs `python -m overpressure`, and where: OVERPRESSURE_PYTHON and OVERPRESSURE_ROOT when set,
/// else the bundled engine, else the `python` on PATH in the checkout this core was built in.
fn engine(app: &AppHandle) -> (PathBuf, PathBuf, bool) {
    let python = std::env::var_os("OVERPRESSURE_PYTHON").map(PathBuf::from);
    let root = std::env::var_os("OVERPRESSURE_ROOT").map(PathBuf::from);
    let checkout = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..");
    match (python, root, bundled(app)) {
        (None, None, Some((python, root))) => (python, root, true),
        (python, root, _) => (
            python.unwrap_or_else(|| "python".into()),
            root.unwrap_or(checkout),
            false,
        ),
    }
}

/// Whether the engine runs its demo, where nothing touches a headset. The installed app (its bundled engine) runs
/// for real unless OVERPRESSURE_APP_DEMO=1; a development build from a checkout runs the demo unless
/// OVERPRESSURE_APP_REAL=1, so that a developer's own headset is touched only on request.
fn demo(bundled: bool, flag: impl Fn(&str) -> bool) -> bool {
    if bundled {
        flag("OVERPRESSURE_APP_DEMO")
    } else {
        !flag("OVERPRESSURE_APP_REAL")
    }
}

/// The engine's command line (demo decides about --demo). The bundled Python sees nothing of the computer's own
/// Python: no user site-packages, no PYTHONPATH or PYTHONHOME.
fn command(app: &AppHandle) -> Command {
    let (python, root, bundled) = engine(app);
    let mut command = Command::new(python);
    command.args(["-m", "overpressure", "serve", "--json"]);
    if demo(bundled, |name| std::env::var(name).as_deref() == Ok("1")) {
        command.arg("--demo");
    }
    if bundled {
        command
            .env("PYTHONNOUSERSITE", "1")
            .env_remove("PYTHONPATH")
            .env_remove("PYTHONHOME");
    }
    command
        .current_dir(root)
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
    let mut child = command(app).spawn()?;
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

/// End the engine: a closed stdin stops a run under way (after a write, never in one) and ends it; wait for that.
fn stop_engine(engine: &Engine) {
    if let Ok(mut stdin) = engine.stdin.lock() {
        stdin.take();
    }
    let child = engine.child.lock().ok().and_then(|mut c| c.take());
    if let Some(mut child) = child {
        let _ = child.wait();
    }
}

/// Install the app's update the engine found (`app_update`): its manifest and its version (semver). The engine
/// ends before the install, the app restarts with the new version; an error leaves everything as it was.
#[tauri::command]
async fn install_update(app: AppHandle, manifest: String, version: String) -> Result<(), String> {
    let handle = app.clone();
    update::install(app, manifest, version, move || {
        stop_engine(&handle.state::<Engine>());
    })
    .await
}

/// Whether the window's close was put to the front end already: the next one closes, as a stuck page must not keep
/// the window open.
#[derive(Default)]
struct Closing {
    asked: AtomicBool,
}

/// Whether a close request goes through now; the first one is put to the front end instead, which answers with
/// `close_window` or `keep_window`.
fn close_goes_through(closing: &Closing) -> bool {
    closing.asked.swap(true, Ordering::SeqCst)
}

/// Close the window: nothing runs, or the owner chose to stop what does.
#[tauri::command]
fn close_window(app: AppHandle, closing: State<'_, Closing>) {
    closing.asked.store(true, Ordering::SeqCst);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.close();
    }
}

/// Keep the window: the owner chose to let what runs go on; the next close asks again.
#[tauri::command]
fn keep_window(closing: State<'_, Closing>) {
    closing.asked.store(false, Ordering::SeqCst);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let engine = start(app.handle())?;
            app.manage(engine);
            app.manage(Closing::default());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if !close_goes_through(&window.state::<Closing>()) {
                    api.prevent_close();
                    let _ = window.emit("close-requested", ());
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            engine_send,
            close_window,
            keep_window,
            install_update
        ])
        .build(tauri::generate_context!())
        .expect("the app could not start")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                stop_engine(&app.state::<Engine>());
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{close_goes_through, demo, Closing};
    use std::sync::atomic::Ordering;

    #[test]
    fn the_first_close_asks_the_page_and_the_second_goes_through() {
        let closing = Closing::default();
        assert!(
            !close_goes_through(&closing),
            "the first close is put to the page"
        );
        assert!(
            close_goes_through(&closing),
            "a second close goes through, whatever the page does"
        );
        closing.asked.store(false, Ordering::SeqCst); // keep_window: the owner chose to stay
        assert!(
            !close_goes_through(&closing),
            "after keep_window the next close asks again"
        );
    }

    #[test]
    fn the_installed_app_runs_for_real_and_a_development_build_its_demo() {
        let none = |_: &str| false;
        assert!(!demo(true, none), "the installed app: real by default");
        assert!(
            demo(false, none),
            "a development build: the demo by default"
        );
        assert!(demo(true, |name| name == "OVERPRESSURE_APP_DEMO"));
        assert!(!demo(false, |name| name == "OVERPRESSURE_APP_REAL"));
        // Each build answers only its own variable.
        assert!(!demo(true, |name| name == "OVERPRESSURE_APP_REAL"));
        assert!(demo(false, |name| name == "OVERPRESSURE_APP_DEMO"));
    }
}
