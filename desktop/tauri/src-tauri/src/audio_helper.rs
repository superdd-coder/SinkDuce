use std::path::PathBuf;
use std::process::Command;

/// One identity and address for the initial helper and watchdog replacements.
pub struct AudioHelper {
    pub path: PathBuf,
    pub url: String,
    pub pid: Option<u32>,
}

impl AudioHelper {
    pub fn configure_backend(&self, command: &mut Command) {
        command
            .env("SINKDUCE_SYSAUDIO_BIN", &self.path)
            .env("SINKDUCE_SYS_AUDIO", &self.url)
            .env_remove("SINKDUCE_SYSAUDIO_PID");
        if let Some(pid) = self.pid {
            command.env("SINKDUCE_SYSAUDIO_PID", pid.to_string());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[test]
    fn delayed_or_failed_start_keeps_the_same_recovery_target() {
        for pid in [None, Some(123)] {
            let helper = AudioHelper {
                path: PathBuf::from("/Example.app/Contents/MacOS/sinkduce-sysaudio"),
                url: "http://127.0.0.1:18950".into(),
                pid,
            };
            let mut command = Command::new("python");
            helper.configure_backend(&mut command);
            let vars: std::collections::HashMap<_, _> = command.get_envs().collect();
            assert_eq!(vars[OsStr::new("SINKDUCE_SYSAUDIO_BIN")], Some(helper.path.as_os_str()));
            assert_eq!(vars[OsStr::new("SINKDUCE_SYS_AUDIO")], Some(OsStr::new(&helper.url)));
            assert_eq!(vars[OsStr::new("SINKDUCE_SYSAUDIO_PID")].map(|v| v.to_string_lossy().into_owned()), pid.map(|p| p.to_string()));
        }
    }
}
