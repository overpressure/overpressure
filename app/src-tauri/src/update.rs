// SPDX-License-Identifier: Unlicense
//! The app's own update (docs/tool.md#the-apps-updates). The engine picks the release (`app_update`); this core
//! reads the manifest that release carries, downloads this system's installer, checks its signature against the
//! key the app was built with (tauri.conf.json's plugins.updater.pubkey; the version it was signed for must be the
//! one the manifest names) and replaces the app: the engine ends first, the app restarts after.
//! A build without a key, as every development build, cannot update itself.

use serde::Serialize;
use tauri::{AppHandle, Emitter, Url};
use tauri_plugin_updater::UpdaterExt;

/// Where a manifest may come from: overpressure's own releases.
const RELEASES: &str = "https://github.com/overpressure/overpressure/releases/download/";

/// How far the download is, for the front end ("update-progress").
#[derive(Clone, Serialize)]
struct Progress {
    done: u64,
    total: Option<u64>,
}

/// Whether this build has the update key: the public half of the key the release workflow signs with.
fn has_key(app: &AppHandle) -> bool {
    let plugins = &app.config().plugins.0;
    plugins
        .get("updater")
        .and_then(|updater| updater.get("pubkey"))
        .and_then(|key| key.as_str())
        .is_some_and(|key| !key.trim().is_empty())
}

/// Nothing, or why this build cannot update itself.
fn key(app: &AppHandle) -> Result<(), String> {
    if !has_key(app) {
        Err("This build of overpressure has no update key, so it cannot update itself: download the new release.".into())
    } else {
        Ok(())
    }
}

/// The manifest's URL, when it is one of overpressure's own releases' files.
fn manifest_url(manifest: &str) -> Result<Url, String> {
    if !manifest.starts_with(RELEASES) || manifest.contains("..") {
        return Err(format!(
            "not a manifest of overpressure's releases: {manifest}"
        ));
    }
    Url::parse(manifest).map_err(|e| e.to_string())
}

/// Download the release @p manifest names, version @p version (semver, as the engine named it), check its
/// signature and install it; `stop_engine` runs before the install. The app restarts with the new version.
pub async fn install(
    app: AppHandle,
    manifest: String,
    version: String,
    stop_engine: impl FnOnce(),
) -> Result<(), String> {
    key(&app)?;
    let url = manifest_url(&manifest)?;
    let wanted = version.clone();
    let updater = app
        .updater_builder()
        .endpoints(vec![url])
        .map_err(|e| e.to_string())?
        // The engine chose the release; install exactly that one (a beta's stable release, too).
        .version_comparator(move |_current, remote| remote.version.to_string() == wanted)
        .build()
        .map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| format!("overpressure {version} has no app for this system"))?;
    let mut done = 0u64;
    let handle = app.clone();
    let bytes = update
        .download(
            move |chunk, total| {
                done += chunk as u64;
                let _ = handle.emit("update-progress", Progress { done, total });
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    stop_engine();
    update.install(bytes).map_err(|e| e.to_string())?;
    app.restart()
}

#[cfg(test)]
mod tests {
    use super::manifest_url;

    #[test]
    fn a_manifest_comes_from_overpressures_releases_only() {
        let own = "https://github.com/overpressure/overpressure/releases/download/2026.12.0/overpressure-app.json";
        assert!(manifest_url(own).is_ok());
        assert!(manifest_url("https://example.com/overpressure-app.json").is_err());
        assert!(
            manifest_url("https://github.com/someone/else/releases/download/1/x.json").is_err()
        );
        assert!(manifest_url(
            "https://github.com/overpressure/overpressure/releases/download/../../x/y.json"
        )
        .is_err());
    }
}
