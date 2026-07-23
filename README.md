# Megaman Recovery Tool

## Demo-praktijktest

De demo bouwt een volledig geïsoleerde herstelomgeving met uitsluitend zelf
gegenereerde sinus-audio. Bestaande MP3's, databases, RAR-bestanden en
Spotify-configuratie worden niet gebruikt of gewijzigd.

Voer vanuit de projectmap in PowerShell uit:

```powershell
python tools/create_demo_recovery_test.py --run
```

Benodigd:

- Python 3.10 of nieuwer;
- de dependencies uit `requirements.txt`;
- FFmpeg, beschikbaar via `PATH` of als `C:\ffmpeg\ffmpeg.exe`;
- 7-Zip als `C:\Program Files\7-Zip\7z.exe`;
- optioneel WinRAR/Rar voor een echte multipart RAR-set.

Als `Rar.exe` beschikbaar is, maakt de demo een echte multipart RAR-set.
Zonder RAR-aanmaaktool gebruikt de demo zichtbaar een gedocumenteerde
7-Zip-listingfixture met dezelfde inventarisgegevens. Spotify wordt altijd
lokaal gemockt; er zijn geen credentials of netwerkverbinding nodig.

Op het scherm verschijnen de gewone scan-, inventaris-, recovery-,
identiteits- en Spotify-overzichten. De afsluitende regel is `PASS` wanneer
alle verwachte aantallen, SQLite-records en playlistgegevens kloppen. Bij een
afwijking verschijnt `FAIL` met de exacte mislukte controle en eindigt het
script met een niet-nul exitcode.

De volledige demomap staat onder `demo_runs` en het absolute pad wordt aan
het einde getoond. Daar staan onder andere:

- `megaman_demo.sqlite3`;
- `spotify_recovery_playlist.json`;
- de originele en gewijzigde MP3-testmappen;
- de RAR-set of listingfixture;
- het tekstrapport onder `reports`.

De demo blijft staan voor inspectie. Verwijder één specifieke demo veilig met:

```powershell
python tools/create_demo_recovery_test.py --cleanup "C:\volledig\pad\naar\de\demomap"
```

Of verwijder alle door dit script gemarkeerde demo's:

```powershell
python tools/create_demo_recovery_test.py --cleanup-all
```
