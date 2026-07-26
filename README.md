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

De hoofdactie **Start** voert één recovery-set buiten de GUI-thread uit. Het
venster toont voor `Analyse`, `PAR2`, `RAR Recovery`, `Validatie`,
`Recovery Items`, `Spotify Search`, `Recovery Review`, `Playlist Sync` en
`Rapport` steeds de
toestand wachtend, actief, voltooid, overgeslagen of mislukt. De algemene
voortgang, huidige stap en gelogde backendmeldingen worden realtime bijgewerkt.

Na Spotify Search opent de **Recovery Review Wizard**. Deze toont uitsluitend
ontbrekende of defecte recovery-items en alle opgeslagen Spotify-kandidaten.
Selecteer de items die later naar een playlist mogen en kies waar nodig één
Spotify-kandidaat. De selectie wordt in SQLite opgeslagen. In deze fase wordt
nadrukkelijk nog geen Spotify-playlist gemaakt; `Playlist Sync` blijft
uitgesteld tot een volgende workflowstap.

Een overtuigende `MATCHED`-kandidaat wordt alleen vooraf geselecteerd wanneer
de confidence minimaal 95% is, geen bijna gelijk scorende concurrent bestaat
en titel/album geen zichtbaar versieconflict opleveren. Low-confidence- en
reviewgevallen blijven leeg. De wizard toont versie-indicaties zoals Live,
Remix, Radio Edit, Extended Mix, Acoustic en Remastered, ondersteunt zoeken,
sorteren en filters, en bewaart checkbox- en kandidaatkeuzes direct. Alleen
een aangevinkt item met een concrete Spotify-URI telt als **Klaar voor
playlist**. Continue waarschuwt wanneer aangevinkte items nog geen bruikbare
match hebben.

Na **Playlist voorbereiden** toont de GUI eerst een aparte bevestiging met de
aantallen geselecteerd, gekoppeld, klaar voor playlist, zonder match en
gedeselecteerd. De playlistnaam kan daar nog worden aangepast. Alleen de knop
**Playlist maken** start de Spotify-mutatie; Terug en Annuleren maken niets.
De synchronisatie gebruikt uitsluitend persistent gereviewde
`selected_spotify_uri`-waarden van aangevinkte items, sorteert op
jaar/week/chartpositie/bestandsnaam en dedupliceert op track-ID of URI. Een
bestaande playlist-ID wordt hergebruikt. Het resultaatscherm toont de echte
Spotify-URL, nieuwe en bestaande tracks, duplicaten, onvolledige keuzes en de
syncstatus. Reviewkeuzes blijven bij API- of netwerkfouten behouden.

Handmatige praktijktest:

1. Haal de branch op met
   `git pull origin feature/salvage-rar-recovery-workflow`.
2. Activeer de virtuele omgeving, bijvoorbeeld met
   `.\.venv\Scripts\Activate.ps1`.
3. Start met `python main.py --gui`.
4. Kies met **Bladeren…** een tijdelijke NZBGet-map met de originele
   RAR/PAR2-set.
5. Controleer vóór Start dat de recovery-set uit database, hoofd-RAR of PAR2
   is afgeleid en niet uit een tijdelijke NZBGet-hash wanneer een betere naam
   beschikbaar is.
6. Klik **Start**. PowerShell blijft leeg; huidige stap, huidige activiteit,
   voortgang en vaste tellerlabels veranderen in het venster.
7. Controleer dat Analyse eindigt, iedere volgende fase pas daarna actief
   wordt en geen fase permanent actief blijft.
8. Verplaats of vergroot het venster tijdens validatie en Spotify Search om
   te controleren dat de interface responsief blijft.
9. Beoordeel in Recovery Review de kandidaten, test **Select All**,
   **Select None** en **Invert Selection**, en controleer dat **Continue**
   pas actief wordt zodra minimaal één item is geselecteerd.
10. Rond de review af. Playlist Sync wordt zichtbaar overgeslagen en er wordt
    in deze fase geen Spotify-playlist aangemaakt.
11. Test een lege of niet-bestaande bron; er verschijnt een waarschuwing en
    er start geen worker.
12. Test zonder Spotify-credentials/netwerk; Spotify Search, Recovery Review
    en Playlist Sync worden gemotiveerd overgeslagen, terwijl rapportage
    beschikbaar blijft.
13. Probeer tijdens een actieve workflow het venster te sluiten. De GUI
    blokkeert sluiten met de melding dat de workflow nog loopt. Sluit opnieuw
    nadat de workflow klaar is; er hoort geen `QThread destroyed`-waarschuwing
    te verschijnen.

Het eindoverzicht toont de recovery-set, recovery-items, alle Spotify-statussen,
nieuwe en reeds aanwezige playlisttracks, playlistnaam en eindstatus. Er worden
geen secrets, access tokens, refresh tokens of autorisatie-URL's gelogd.

Het hoofdvenster toont uitsluitend de begeleide workflow. De achterliggende
losse CLI-, review-, recovery- en Spotifyfuncties blijven beschikbaar voor
CLI-gebruik en een eventueel later handmatig reviewscherm.

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

Rebuilt/repaired multipart-sets worden als `COMPLETE`, `PARTIAL`,
`SINGLE_VOLUME` of `INVALID` geclassificeerd. Iedere bruikbare herstelde bron
wordt met beide tools geprobeerd, maar vervangt de originele set nooit: ook de
originele volumes krijgen altijd beide extractiepogingen. Exitcode 0 bepaalt
niet de eindstatus; de vergelijking met de verwachte MP3-inventaris doet dat.

De eindclassificatie combineert deze vergelijking met opgeslagen
FFmpeg-validatiefouten en 0-byte-feiten op genormaliseerd relatief pad.
`Fysiek aanwezig` betekent daarom niet automatisch `volledig goed`:
FFmpeg-fouten, nul-byte en andere onleesbare bestanden blijven unieke
recovery-items, ook wanneer het bestand wel in `extracted` staat.

- `COMPLETE`: alle verwachte MP3’s zijn bruikbaar.
- `SALVAGED`: niet volledig gerepareerd, maar alle MP3’s zijn gered.
- `PARTIAL`: een deel is gered en recovery-items zijn nodig.
- `FAILED`: niets bruikbaars kon worden uitgepakt.

De volledige vergelijking blijft in SQLite; console en GUI tonen compacte
aantallen. Handmatige Spotify-keuzes blijven behouden.

# Spotify Search Engine (fase 1)

De geïsoleerde backend in `core/spotify` zoekt recovery-items uitsluitend via
de officiële Spotify Web API. Configureer lokaal:

```powershell
$env:SPOTIFY_CLIENT_ID="..."
$env:SPOTIFY_CLIENT_SECRET="..."
$env:SPOTIFY_MARKET="NL"
```

De engine probeert achtereenvolgens een veldzoekopdracht, artiest plus titel
en alleen titel. Alle kandidaten worden genormaliseerd en gescoord op artiest,
titel en, wanneer lokaal leesbaar, duur. Alleen de hoogste score wordt op het
recovery-item opgeslagen als `MATCHED`, `LOW_CONFIDENCE`, `NOT_FOUND` of
`MANUAL_REVIEW`.

De zoekfase wijzigt geen playlists, GUI of mediabestanden.

De zoekengine verwerkt altijd precies één recovery-set. Geef bij voorkeur
`recovery_set_id` of `archive_set_name` door; zonder selectie wordt alleen de
meest recent bijgewerkte geldige set gekozen. Reeds automatisch verwerkte
items worden standaard overgeslagen en zijn met `force=True` opnieuw te
zoeken. Handmatige keuzes worden nooit overschreven. Batches boven 500 items
vereisen expliciet `allow_large_batch=True`.

```python
voer_spotify_search_uit(
    database,
    archive_set_name="Jaarcollectie",
    force=False,
    allow_large_batch=False,
)
```

# Spotify Playlist Manager

Playlistbeheer gebruikt Authorization Code Flow met de scopes
`playlist-read-private` en `playlist-modify-private`. Configureer in de
Spotify Developer App exact deze redirect URI:

```text
http://127.0.0.1:8888/callback
```

Bij de eerste autorisatie opent de standaardbrowser. De tool bewaart access
token, refresh token en verloopmoment standaard in:

```text
%LOCALAPPDATA%\Megaman Recovery Tool\spotify_user_tokens.json
```

Met `SPOTIFY_TOKEN_CACHE` kan een ander lokaal cachepad worden ingesteld. Het
bestand wordt atomisch geschreven en alleen voor deze lokale gebruiker
bedoeld. Verlopen tokens worden automatisch vernieuwd; handmatig instellen van
`SPOTIFY_ACCESS_TOKEN` is niet nodig.

`sync_playlist` werkt altijd op één expliciet geselecteerde recovery-set. De
functie hergebruikt eerst de opgeslagen playlist-ID, zoekt anders in de
playlists van de huidige gebruiker naar exact dezelfde naam en maakt als
laatste mogelijkheid een privéplaylist. Alleen unieke recovery-items met
status `MATCHED` worden toegevoegd; opnieuw uitvoeren is veilig.

```python
sync_playlist(database, archive_set_name="Jaarcollectie")
```

# YouTube-bronselectie (fase 2)

Recovery-items zonder gekozen Spotify-match kunnen in de Recovery Review
Wizard via **Search YouTube** worden opgezocht. Hiervoor wordt uitsluitend de
officiële YouTube Data API v3 gebruikt; configureer de sleutel lokaal en neem
deze nooit op in Git:

```powershell
$env:YOUTUBE_API_KEY="..."
python main.py --gui
```

De wizard toont maximaal tien gededupliceerde kandidaten, scorecomponenten en
waarschuwingen. Een keuze of “Geen geschikte YouTube-bron” wordt direct in
SQLite opgeslagen en bij heropenen hersteld. **Search Again** behoudt een
bestaande keuze, ook bij een API- of netwerkfout. Deze fase zoekt en bewaart
alleen metadata: zij roept geen yt-dlp of FFmpeg-download aan, schrijft geen
audio of ID3-tags en verplaatst geen bestanden.

# Download Queue (fase 3)

Na het kiezen van een geldige YouTube-bron zet **Prepare Downloads** ieder
geselecteerd en afgerond recovery-item precies eenmaal in de persistente
downloadqueue. De queue kan worden geordend, gepauzeerd, hervat, geannuleerd
en na een herstart veilig worden herladen.

**Start Queue is in deze fase uitsluitend een statussimulator.** Met korte
Qt-timers doorloopt een job `WAITING`, `QUEUED`, `PREPARING`, `RUNNING` en
`COMPLETED`. Er wordt geen netwerkverbinding gemaakt, geen subprocess gestart,
geen yt-dlp of FFmpeg aangeroepen en geen audio- of ander mediabestand
geschreven. Een job die tijdens afsluiten `RUNNING` was, wordt bij de volgende
start teruggezet naar `WAITING`.
