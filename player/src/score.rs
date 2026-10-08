//! The .wwm.json script written by `wwm arrange`.
use std::collections::HashMap;
use std::fs;

use serde::Deserialize;

#[derive(Deserialize, Debug)]
pub struct Script {
    pub version: u32,
    #[serde(default)]
    pub mode: String,
    #[serde(default = "default_hold")]
    pub hold_ms: u64,
    pub keymap: KeyMapSpec,
    pub events: Vec<Event>,
}

fn default_hold() -> u64 {
    20
}

#[derive(Deserialize, Debug)]
pub struct KeyMapSpec {
    #[serde(default)]
    pub name: String,
    pub scancodes: HashMap<String, u16>,
}

#[derive(Deserialize, Debug)]
pub struct Event {
    pub t_ms: u64,
    pub groups: Vec<Group>,
    #[serde(default)]
    pub notes: Vec<String>,
}

#[derive(Deserialize, Debug)]
pub struct Group {
    #[serde(rename = "mod")]
    pub modifier: Option<String>,
    pub keys: Vec<String>,
}

impl Script {
    pub fn load(path: &str) -> Result<Script, String> {
        let text = fs::read_to_string(path).map_err(|e| format!("cannot read {path}: {e}"))?;
        let script: Script = serde_json::from_str(&text).map_err(|e| format!("{path} is not a wwm script: {e}"))?;
        if script.version != 1 {
            return Err(format!("unsupported script version {}", script.version));
        }
        for (i, ev) in script.events.iter().enumerate() {
            for g in &ev.groups {
                if let Some(m) = &g.modifier {
                    if !script.keymap.scancodes.contains_key(m) {
                        return Err(format!("event {i}: modifier {m:?} has no scan code"));
                    }
                }
                for k in &g.keys {
                    if !script.keymap.scancodes.contains_key(k) {
                        return Err(format!("event {i}: key {k:?} has no scan code"));
                    }
                }
            }
        }
        Ok(script)
    }

    pub fn duration_ms(&self) -> u64 {
        self.events.last().map(|e| e.t_ms).unwrap_or(0)
    }
}
