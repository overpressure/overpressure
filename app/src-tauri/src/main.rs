// SPDX-License-Identifier: Unlicense
// No console window on Windows in a release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    overpressure_app_lib::run();
}
