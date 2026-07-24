# Megaman Recovery Tool

## PAR2-verificatie (alleen-lezen)

Tijdens `--analyze` koppelt Megaman Recovery Tool aangetroffen PAR2-sets aan
de bijbehorende RAR-sets en voert het uitsluitend een verificatieopdracht uit.
Er wordt nooit automatisch gerepareerd of uitgepakt. De uitkomst wordt als
`COMPLETE`, `REPAIRABLE`, `NOT_REPAIRABLE` of `UNKNOWN` opgeslagen in SQLite
en opgenomen in de console- en tekstrapportage.

De commandline-verifier wordt in deze volgorde gezocht:

1. het expliciete pad in `PAR2_PATH`;
2. `par2.exe`, `par2`, `par2j64.exe` of `par2j.exe` via `PATH`;
3. bekende installatielocaties van SABnzbd, MultiPar en QuickPar.

Alleen commandlineprogramma's worden gebruikt. De grafische
`QuickPar.exe` wordt expliciet geweigerd.

Een expliciet pad instellen en daarna analyseren:

```powershell
$env:PAR2_PATH = "C:\Program Files\SABnzbd\win\par2\par2.exe"
& $env:PAR2_PATH
python main.py --analyze "C:\ProgramData\NZBGet\intermediate\4fe20a6a4f204822ed17e88d.#2"
```

`PAR2_PATH` geldt hiermee alleen voor de huidige PowerShell-sessie. Permanent
instellen voor de huidige gebruiker kan met:

```powershell
[Environment]::SetEnvironmentVariable(
    "PAR2_PATH",
    "C:\Program Files\SABnzbd\win\par2\par2.exe",
    "User"
)
```

Open daarna zo nodig een nieuwe PowerShell-sessie. De commandline
`par2.exe` uit SABnzbd is geschikt; `QuickPar.exe` zelf niet. Automatische
PAR2-reparatie en RAR-extractie zijn bewust nog niet ingebouwd en horen bij
een latere stap.

Zonder gevonden verifier gaat de overige analyse door en verschijnt:

```text
PAR2-tool niet gevonden: stel PAR2_PATH in of installeer een commandline PAR2-tool.
```

De verificatie heeft per PAR2-set een time-out van 120 seconden. Een
time-out, startfout of niet-herkende tooluitvoer wordt als `UNKNOWN`
geregistreerd; één mislukte set stopt de andere sets niet. Zowel het gebruikte
executablepad en commando als returncode, duur, samenvatting en begrensde
stdout/stderr worden voor diagnose bewaard.

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

# RAR-extractie

Na een analyse kan een downloadmap veilig worden uitgepakt:

```powershell
python main.py --extract "C:\pad\naar\downloadmap"
```

De opdracht leest de laatste opgeslagen PAR2-verificatie uit
`megaman_recovery.db`. Alleen RAR-sets met status `COMPLETE` worden vanaf
`part01.rar` uitgepakt. `REPAIRABLE`, `NOT_REPAIRABLE`, `UNKNOWN` en een
ontbrekende verificatie worden overgeslagen. De uitvoer komt standaard in de
submap `extracted`; bestaande bestanden worden niet overschreven. Resultaten
en skips worden opgeslagen in de SQLite-tabel `extraction_results`.

# PAR2-reparatie

Na een analyse kunnen uitsluitend als `REPAIRABLE` aangemerkte PAR2-datasets
expliciet worden gerepareerd met de officiële CLI-optie `--repair "<map>"`:

```powershell
python main.py --repair "C:\pad\naar\downloadmap"
```

De opdracht gebruikt dezelfde `PAR2_PATH`-, PATH- en vaste-paddetectie als de
read-only verificatie. `COMPLETE`, `NOT_REPAIRABLE` en andere statussen worden
overgeslagen. Na een geslaagde repair-opdracht wordt de dataset automatisch
opnieuw geverifieerd en worden de actuele status, procesuitvoer, tijden,
exitcode en eventuele foutmelding in SQLite opgeslagen. `--analyze` blijft
read-only en start nooit een reparatie.

# Desktop-GUI

Installeer eerst alle afhankelijkheden:

```powershell
python -m pip install -r requirements.txt
```

Start daarna de PySide6-interface:

```powershell
python main.py --gui
```

De GUI biedt vier expliciete acties:

- **Analyseren** gebruikt de bestaande read-only analyse.
- **Repareren** verwerkt uitsluitend PAR2-datasets met status `REPAIRABLE`.
- **Uitpakken** verwerkt uitsluitend datasets met status `COMPLETE`.
- **Rapport tonen** toont het laatst opgeslagen rapport.

Repareren kan bronbestanden wijzigen of aanmaken. Uitpakken maakt bestanden
aan in de extractiedoelmap. Daarom vraagt de GUI voor beide acties altijd om
bevestiging; analyse start nooit automatisch een reparatie of extractie.

# Slim zoeken en Spotify-versies

Stel uitsluitend lokaal de officiële Spotify API-credentials in:

```powershell
$env:SPOTIFY_CLIENT_ID="..."
$env:SPOTIFY_CLIENT_SECRET="..."
```

Zoek nieuwe of nog niet definitief beoordeelde recovery-items met:

```powershell
python main.py --spotify-search "C:\pad\naar\downloadmap"
python main.py --spotify-retry "C:\pad\naar\downloadmap"
```

De eerste opdracht probeert originele metadata, opgeschoonde metadata,
bestandsnamen, basistitels en versie-informatie. De retry verwerkt uitsluitend
`NOT_FOUND` en `AMBIGUOUS`. Kandidaten worden ontdubbeld, gerangschikt en met
hun scoringsonderdelen lokaal in SQLite opgeslagen.

- `FOUND`: één overtuigende kandidaat met passende versie.
- `AMBIGUOUS`: meerdere tracks of officiële versies zijn aannemelijk.
- `NOT_FOUND`: geen kandidaat haalt de minimumscore.
- `INSUFFICIENT_IDENTITY`: artiest/titel zijn onvoldoende betrouwbaar.
- `MANUAL`: de gebruiker heeft een kandidaat gekozen.
- `REVIEWED_NONE`: de gebruiker heeft alle kandidaten afgewezen.

Radio Edit, Extended Mix, Original Mix en specifieke remixers worden apart
herkend en wegen zwaar in de score. Handmatige keuzes worden nooit automatisch
overschreven. De tool gebruikt bewust geen YouTube: benamingen en versies zijn
daarvoor onvoldoende betrouwbaar. Spotify-kandidaten, scores, zoekopdrachten
en keuzes blijven lokaal in SQLite; credentials worden niet opgeslagen of
gelogd. Deze workflow wijzigt geen MP3-bestanden.

# Fouttolerante RAR-salvage

`NOT_REPAIRABLE` betekent niet dat niets meer gered kan worden. Na PAR2
probeert de salvage-workflow zo nodig non-interactieve RAR/WinRAR-recovery.
Daarna probeert eerst RAR/WinRAR en vervolgens 7-Zip fouttolerant uit te
pakken. De tweede poging vult de bestaande uitvoer aan en verwijdert geen
bestanden uit de eerste poging:

```powershell
python main.py --salvage-rar "C:\downloads"
python main.py --salvage-rar "C:\downloads" --workspace "D:\recovery"
python main.py --salvage-rar "C:\downloads" --rar-set "Jaarcollectie1999"
python main.py --salvage-rar "C:\downloads" --skip-par2 --skip-winrar
```

Stel afwijkende toolpaden in met `WINRAR_PATH` en `SEVENZIP_PATH`. Voor
RAR/WinRAR heeft de consoletool `Rar.exe` de voorkeur. Anders worden
standaardinstallaties en daarna PATH doorzocht. Iedere recovery-poging krijgt
een eigen runmap onder `recovery`; de samengevoegde uitvoer blijft in
`extracted`. Bestaande salvage-output en originele RAR-volumes worden nooit
verwijderd of overschreven.

- `COMPLETE`: alle verwachte MP3’s zijn bruikbaar.
- `SALVAGED`: niet volledig gerepareerd, maar alle MP3’s zijn gered.
- `PARTIAL`: een deel is gered en recovery-items zijn nodig.
- `FAILED`: niets bruikbaars kon worden uitgepakt.

De volledige vergelijking blijft in SQLite; console en GUI tonen compacte
aantallen. Handmatige Spotify-keuzes blijven behouden.
