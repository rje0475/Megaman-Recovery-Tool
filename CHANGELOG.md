# Changelog

Alle belangrijke wijzigingen worden in dit bestand bijgehouden volgens
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-07-26

### Added

- Fouttolerante PAR2-, WinRAR/RAR- en 7-Zip-salvageworkflow.
- Spotify- en YouTube-matching met handmatige Recovery Review.
- Persistente downloadqueue, audioprocessing en metadatafinalisatie.
- PySide6-workflow, centrale instellingen, logging en crashrapportage.
- Projectbackup, restore, health check, self-test en release-buildconfiguratie.

### Security

- Tokens blijven buiten exports en logs; bekende secretvelden worden geredacteerd.
- Atomische bestandsoperaties, ZIP-pathvalidatie en database-integriteitscontrole.
