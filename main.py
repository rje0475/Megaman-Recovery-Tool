from pathlib import Path

from database import maak_database
from report import maak_rapport
from rar import zoek_part01_bestanden
from rar import test_rar
from scanner import controleer_mp3_bestanden
from scanner import zoek_mp3_bestanden


print("===================================")
print("     Megaman Recovery Tool v1.0")
print("===================================")

# --------------------------------------------------
# Database
# --------------------------------------------------

database = maak_database()

# --------------------------------------------------
# MP3 map
# --------------------------------------------------

mp3_pad = input("\nSleep de map met de UITGEPAKTE MP3's hierheen:\n")
mp3_pad = mp3_pad.strip('"')

mp3_map = Path(mp3_pad)

if not mp3_map.exists():
    print("\n❌ MP3-map bestaat niet.")
    input("\nDruk op Enter...")
    exit()

# --------------------------------------------------
# RAR map
# --------------------------------------------------

rar_pad = input("\nSleep nu de map met de ORIGINELE RAR's hierheen:\n")
rar_pad = rar_pad.strip('"')

rar_map = Path(rar_pad)

if not rar_map.exists():
    print("\n❌ RAR-map bestaat niet.")
    input("\nDruk op Enter...")
    exit()

# --------------------------------------------------
# MP3 scan
# --------------------------------------------------

print("\n🔍 MP3's scannen...")

mp3_bestanden = zoek_mp3_bestanden(mp3_map)

controleer_mp3_bestanden(
    mp3_bestanden,
    mp3_map,
    database
)

# --------------------------------------------------
# RAR scan
# --------------------------------------------------

print("🔍 RAR-sets zoeken...")

part01_bestanden = zoek_part01_bestanden(rar_map)

for rar in part01_bestanden:
    test_rar(rar, database)

# --------------------------------------------------
# Tellingen
# --------------------------------------------------

totaal = len(database)

goed = sum(
    1
    for gegevens in database.values()
    if (
        not gegevens["nul_bytes"]
        and gegevens["rar"]["status"] != "ERROR"
        and gegevens["ffmpeg"]["status"] != "ERROR"
    )
)

nul_bytes = sum(
    1
    for gegevens in database.values()
    if gegevens["nul_bytes"]
)

rar_fouten = sum(
    1
    for gegevens in database.values()
    if gegevens["rar"]["status"] == "ERROR"
)

ffmpeg_fouten = sum(
    1
    for gegevens in database.values()
    if gegevens["ffmpeg"]["status"] == "ERROR"
)

# --------------------------------------------------
# Resultaat
# --------------------------------------------------

print("\n===================================")
print("SCAN RESULTAAT")
print("===================================")

print(f"MP3 bestanden      : {totaal}")
print(f"Bestanden OK       : {goed}")
print(f"0-byte bestanden   : {nul_bytes}")
print(f"RAR fouten         : {rar_fouten}")
print(f"FFmpeg fouten      : {ffmpeg_fouten}")

# --------------------------------------------------
# 0-byte bestanden
# --------------------------------------------------

if nul_bytes:

    print("\n0-byte bestanden:\n")

    for gegevens in database.values():

        if gegevens["nul_bytes"]:
            print(gegevens["relatief_pad"])

# --------------------------------------------------
# RAR fouten
# --------------------------------------------------

if rar_fouten:

    print("\n===================================")
    print("RAR FOUTEN")
    print("===================================\n")

    for gegevens in database.values():

        if gegevens["rar"]["status"] == "ERROR":

            print(f"[{gegevens['rar']['type']}]")
            print(f"    {gegevens['relatief_pad']}\n")

# --------------------------------------------------
# FFmpeg fouten
# --------------------------------------------------

if ffmpeg_fouten:

    print("\n===================================")
    print("FFMPEG FOUTEN")
    print("===================================\n")

    for gegevens in database.values():

        if gegevens["ffmpeg"]["status"] == "ERROR":

            print(f"[{gegevens['ffmpeg']['type']}]")
            print(f"    {gegevens['relatief_pad']}\n")

# --------------------------------------------------
# Rapport
# --------------------------------------------------

rapport = maak_rapport(
    mp3_map,
    database
)

print("\n===================================")
print("📄 Rapport opgeslagen:")
print(rapport)
print("===================================")

input("\nDruk op Enter om af te sluiten...")