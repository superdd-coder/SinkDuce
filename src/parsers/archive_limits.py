"""Resource limits shared by network results and ZIP-based Office documents."""

from contextlib import contextmanager
from zipfile import BadZipFile, ZipFile, _ECD_ENTRIES_TOTAL, _ECD_SIZE, _EndRecData

MAX_ENTRIES = 8192
MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


def check_zip(archive: ZipFile) -> None:
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise ValueError("Archive entry count exceeds the parsing limit")
    if len({info.filename for info in members}) != len(members):
        raise ValueError("Archive contains ambiguous duplicate entries")
    if any(info.file_size > MAX_ENTRY_BYTES for info in members):
        raise ValueError("Archive entry exceeds the parsing size limit")
    if sum(info.file_size for info in members) > MAX_EXPANDED_BYTES:
        raise ValueError("Expanded archive exceeds the parsing size limit")


@contextmanager
def checked_archive(stream):
    """Bound metadata allocation before ZipFile constructs every central directory entry."""
    # Use the same footer and ZIP64 interpretation as ZipFile's own reader.
    footer = _EndRecData(stream)
    if footer is None:
        raise BadZipFile("Invalid document archive")
    if footer[_ECD_ENTRIES_TOTAL] > MAX_ENTRIES or footer[_ECD_SIZE] > MAX_DIRECTORY_BYTES:
        raise ValueError("Archive directory exceeds the parsing limit")
    with ZipFile(stream) as archive:
        check_zip(archive)
        yield archive
