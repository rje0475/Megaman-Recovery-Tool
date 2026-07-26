# Contributing

Maak een featurebranch vanaf actuele `main`, houd wijzigingen klein en voeg
regressietests toe. Recovery-, netwerk- en externe tooltests moeten mocks of
geïsoleerde fixtures gebruiken. Commit nooit tokens, API-keys, lokale databases,
downloads of gebruikersaudio.

Voer vóór een pull request uit:

```powershell
python -m pytest
python -m compileall -q core gui tests
python main.py --help
python main.py --demo
git diff --check
```

Beschrijf veiligheidsimpact, migraties en handmatige teststappen in de PR.
