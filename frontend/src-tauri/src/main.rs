// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;
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
                let sidecar_command = app.shell().sidecar("backend")
                    .expect("backend sidecar not found");
                let (mut rx, child) = sidecar_command
                    .spawn()
                    .expect("Failed to spawn backend sidecar");

                // Keep the child alive for the duration of the app
                *app.state::<BackendProcess>().0.lock().unwrap() = Some(child);

                // Forward backend stdout/stderr to the Tauri log
                tauri::async_runtime::spawn(async move {
                    use tauri_plugin_shell::process::CommandEvent;
                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line) => {
                                print!("[backend] {}", String::from_utf8_lossy(&line));
                            }
                            CommandEvent::Stderr(line) => {
                                eprint!("[backend] {}", String::from_utf8_lossy(&line));
                            }
                            _ => {}
                        }
                    }
                });
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
