//! Use the desktop's private conversion runtime, independently of host apps.
use std::path::Path;
use std::process::Command;

pub fn configure_backend(command: &mut Command, app_root: &Path) {
    if let Some(resources) = app_root.parent() {
        command.env(
            "SINKDUCE_LIBREOFFICE",
            resources.join("libreoffice/LibreOffice.app/Contents/MacOS/soffice"),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[test]
    fn backend_gets_the_bundled_writer_path_even_with_spaces() {
        let mut command = Command::new("python");
        configure_backend(&mut command, Path::new("/Example App/resources/app"));
        let value = command.get_envs().find(|(key, _)| *key == OsStr::new("SINKDUCE_LIBREOFFICE"));
        assert_eq!(value.map(|(_, value)| value), Some(Some(OsStr::new(
            "/Example App/resources/libreoffice/LibreOffice.app/Contents/MacOS/soffice"
        ))));
    }
}
