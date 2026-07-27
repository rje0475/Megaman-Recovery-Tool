"""Veilige ZIP-backup en restore zonder downloads, caches of tijdelijke data."""

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class BackupResult:
    path: Path
    files: tuple[str, ...]


class BackupError(RuntimeError):
    pass


class ProjectBackupManager:
    def __init__(self, project_root=Path.cwd(), database_path="megaman_recovery.db",
                 settings_path="config/settings.json", logs_path="logs"):
        self.root = Path(project_root).resolve()
        self.database_path = self._resolve(database_path)
        self.settings_path = self._resolve(settings_path)
        self.logs_path = self._resolve(logs_path)

    def _resolve(self, path):
        path = Path(path)
        return path.resolve() if path.is_absolute() else (self.root / path).resolve()

    def create(self, target, include_logs=False):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        entries = []
        for source, archive_name in ((self.database_path, "database/megaman_recovery.db"),
                                     (self.settings_path, "config/settings.json")):
            if source.is_file(): entries.append((source, archive_name))
        if include_logs and self.logs_path.is_dir():
            entries.extend((path, f"logs/{path.relative_to(self.logs_path).as_posix()}")
                           for path in self.logs_path.rglob("*") if path.is_file())
        if not entries:
            raise BackupError("Geen database of settingsbestand gevonden voor backup.")
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for source, name in entries: archive.write(source, name)
            os.replace(temporary, target)
        except (OSError, zipfile.BadZipFile) as error:
            temporary.unlink(missing_ok=True)
            raise BackupError(f"Backup maken mislukt: {error}") from error
        return BackupResult(target, tuple(name for _, name in entries))

    def restore(self, source, restore_logs=False):
        source = Path(source)
        if not source.is_file(): raise BackupError("Backupbestand bestaat niet.")
        with tempfile.TemporaryDirectory(prefix="megaman-restore-") as temp:
            staging = Path(temp)
            try:
                with zipfile.ZipFile(source) as archive:
                    allowed = {"database/megaman_recovery.db", "config/settings.json"}
                    names = []
                    for info in archive.infolist():
                        name = PurePosixPath(info.filename)
                        if name.is_absolute() or ".." in name.parts:
                            raise BackupError("Backup bevat een onveilig pad.")
                        normalized = name.as_posix()
                        if normalized in allowed or (restore_logs and normalized.startswith("logs/")):
                            archive.extract(info, staging); names.append(normalized)
            except zipfile.BadZipFile as error:
                raise BackupError("Ongeldig ZIP-backupbestand.") from error
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            mapping = (("database/megaman_recovery.db", self.database_path),
                       ("config/settings.json", self.settings_path))
            restored = []
            for name, destination in mapping:
                staged = staging / Path(name)
                if not staged.is_file(): continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists(): shutil.copy2(destination, destination.with_suffix(destination.suffix + f".before-restore-{timestamp}"))
                temporary = destination.with_suffix(destination.suffix + ".restore-tmp")
                shutil.copy2(staged, temporary); os.replace(temporary, destination)
                restored.append(name)
            if restore_logs and (staging / "logs").is_dir():
                self.logs_path.mkdir(parents=True, exist_ok=True)
                for path in (staging / "logs").rglob("*"):
                    if path.is_file():
                        destination = self.logs_path / path.relative_to(staging / "logs")
                        destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, destination)
                        restored.append(f"logs/{path.relative_to(staging / 'logs').as_posix()}")
            if not restored: raise BackupError("Backup bevat geen herstelbare projectbestanden.")
            return tuple(restored)
