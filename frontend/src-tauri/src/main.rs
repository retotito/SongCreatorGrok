// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

#[allow(dead_code)] // only used in release builds (sidecar not started in dev)
struct BackendProcess(Mutex<Option<CommandChild>>);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            // In debug/dev mode there is no bundled sidecar binary —
            // rely on the manually-started FastAPI backend instead.
            #[cfg(not(debug_assertions))]
            {
                // Kill any stale process already holding port 8001
                // (e.g. a leftover dev backend) so the sidecar can bind cleanly.
                let _ = std::process::Command::new("sh")
                    .args(["-c", "lsof -ti:8001 | xargs kill -9 2>/dev/null || true"])
                    .status();

                // Resolve log path: ~/Library/Logs/UltrastarCreator/backend.log
                let log_dir = app.path().app_log_dir()
                    .unwrap_or_else(|_| std::path::PathBuf::from("/tmp"));
                let _ = std::fs::create_dir_all(&log_dir);
                let log_path = log_dir.join("backend.log");

                // Print to stdout so terminal launch shows it too
                eprintln!("[tauri] Backend log → {}", log_path.display());

                let sidecar_command = app.shell().sidecar("backend")
                    .expect("backend sidecar not found");
                let (mut rx, child) = sidecar_command
                    .spawn()
                    .expect("Failed to spawn backend sidecar");

                // Keep the child alive for the duration of the app
                *app.state::<BackendProcess>().0.lock().unwrap() = Some(child);

                // Write backend stdout/stderr to log file
                tauri::async_runtime::spawn(async move {
                    use tauri_plugin_shell::process::CommandEvent;
                    use std::io::Write;
                    let mut log_file = std::fs::OpenOptions::new()
                        .create(true).append(true).open(&log_path)
                        .ok();

                    // Write a startup marker
                    if let Some(ref mut f) = log_file {
                        let ts = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .map(|d| d.as_secs()).unwrap_or(0);
                        let _ = writeln!(f, "\n--- sidecar start (t={ts}) ---");
                    }

                    while let Some(event) = rx.recv().await {
                        let line_bytes = match &event {
                            CommandEvent::Stdout(l) => Some(l.clone()),
                            CommandEvent::Stderr(l) => Some(l.clone()),
                            _ => None,
                        };
                        if let Some(bytes) = line_bytes {
                            let text = String::from_utf8_lossy(&bytes);
                            // Also print to terminal (useful when launched from Terminal.app)
                            eprint!("[backend] {}", text);
                            if let Some(ref mut f) = log_file {
                                let _ = f.write_all(&bytes);
                                let _ = f.flush();
                            }
                        }
                    }
                });
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
