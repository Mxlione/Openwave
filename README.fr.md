# 📡 OpenWave

> Projet open source de détection, analyse et lecture des diffusions radio et TV accessibles publiquement.

[![CI](https://github.com/Mxlione/Openwave/actions/workflows/ci.yml/badge.svg)](https://github.com/Mxlione/Openwave/actions/workflows/ci.yml)
![Statut](https://img.shields.io/badge/statut-pre--alpha-orange)
![Licence](https://img.shields.io/badge/licence-MIT-green)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Contributions](https://img.shields.io/badge/contributions-bienvenues-brightgreen)

*Version anglaise : [README.md](README.md).*

OpenWave n'est pas un simple lecteur VLC. C'est une plateforme capable de **scanner les
fréquences**, **détecter les services disponibles** (stations FM, chaînes TV) et **les présenter à
l'utilisateur** dans une interface claire, prêts à être écoutés ou regardés.

---

## 📸 À quoi ça ressemble

Un scan de la bande simulée, sans aucun récepteur branché. Chaque image vient d'une exécution
réelle du programme : `scripts/capture_cli.py` et `scripts/capture_interface.py` les regénèrent.

![openwave scan fm --demo](docs/images/cli-scan-fm.svg)

| | |
|---|---|
| ![La liste des stations](docs/images/ui-stations.png) | ![Le spectre en direct](docs/images/ui-spectrum.png) |
| Les stations trouvées par un scan, avec signal, mode et nom RDS | Le spectre et la cascade, en direct via WebSocket |

![Chaînes de télévision](docs/images/ui-channels.png)

---

## 🎯 Objectifs

- Scanner automatiquement la bande FM et détecter les stations présentes
- Scanner les multiplex TV (DVB-T) et lister les chaînes et services
- Afficher la fréquence, la puissance du signal et le spectre
- Lancer l'écoute ou le visionnage en un clic grâce à libVLC
- Rester modulaire : un nouveau récepteur ou un nouveau format se branche sans toucher au reste

---

## ✨ Fonctionnalité centrale

L'utilisateur branche un récepteur compatible (RTL-SDR ou tuner DVB-T), lance un scan, puis choisit
ce qu'il veut écouter ou regarder.

```
RTL-SDR / tuner DVB-T
          ↓
      OpenWave
          ↓
       [SCAN]
          ↓
┌─────────────────────────┐
│ 📻 FM                   │
│  88.1 MHz   Station A   │
│  92.4 MHz   Station B   │
│ 100.7 MHz   Station C   │
│                         │
│ 📺 TV                   │
│ Canal 1                 │
│ Canal 2                 │
│ Canal 3                 │
└─────────────────────────┘
```

Un clic sur **Regarder** ou **Écouter** envoie le flux à libVLC.

---

## 🚦 État du projet

OpenWave est en développement initial. Rien n'est encore publié.

| Domaine | État |
|---|---|
| Dépôt, CI, guide de contribution | 🟢 en place |
| Couche d'abstraction matérielle (`sdr/`) | 🟢 en place |
| Récepteurs simulés (IQ synthétiques, rejeu MPEG-TS) | 🟢 en place |
| Scan de la bande FM (v0.1) | 🟢 en place |
| Démodulation FM et écoute (v0.2) | 🟢 en place |
| Décodage RDS (v0.3) | 🟢 en place |
| Scan DVB-T (v0.4) | 🟢 en place |
| API HTTP (v0.5) | 🟢 en place |
| Interface Angular (v0.6) | 🟢 en place |
| Spectre temps réel (v0.7) | 🟢 en place |
| Site de documentation et packaging (v1.0) | 🟢 en place |
| Validation sur du matériel réel | 🔴 personne n'a essayé |

**Point d'honnêteté sur le matériel.** Le mainteneur ne dispose pour l'instant ni de clé RTL-SDR ni
de tuner DVB-T. Chaque étape de traitement du signal est donc développée et testée contre des
**récepteurs simulés** — IQ synthétiques pour la FM et le RDS, flux de transport enregistrés pour le
DVB-T — qui tournent en CI à chaque commit. Les drivers réels sont écrits d'après les API des
bibliothèques mais ne sont **pas encore validés sur du matériel physique**. Si tu possèdes un
récepteur, valider un driver est la contribution la plus utile possible aujourd'hui : voir les
issues étiquetées `hardware validation`.

---

## 🏗️ Architecture générale

```
                       ┌──────────────────────┐
                       │      OPENWAVE        │
                       │  Projet OPEN SOURCE  │
                       └──────────┬───────────┘
                                  │
                            📡 ANTENNE
                                  │
                   ┌──────────────┴──────────────┐
                   │                             │
              📻 FM RADIO                    📺 TV
                   │                             │
           ┌───────▼───────┐             ┌───────▼──────┐
           │   SDR / FM    │             │ Tuner DVB /  │
           │    Tuner      │             │     SDR      │
           └───────┬───────┘             └───────┬──────┘
                   │                             │
                   ▼                             ▼
            🔎 Détection FM              📦 Multiplex TV (MUX)
                   │                             │
                   ▼                             ▼
           ┌───────────────┐             🔎 Détection des
           │ Fréquence     │             chaînes / services
           │ Puissance     │                     │
           │ Stéréo        │                     ▼
           │ RDS*          │             ┌───────────────┐
           └───────┬───────┘             │ TV 1 · TV 2   │
                   │                     │ TV 3 · Radio 1│
                   ▼                     └───────┬───────┘
              🎵 AUDIO                           │
                   │                             │
                   └──────────────┬──────────────┘
                                  ▼
                       ┌──────────────────────┐
                       │       PLAYBACK       │
                       │  WAV framing         │
                       │  libVLC              │
                       └──────────┬───────────┘
                                  ▼
                       ┌──────────────────────┐
                       │     OPENWAVE UI      │
                       │ 📻 Stations FM       │
                       │ 📺 Chaînes TV        │
                       │ 📊 Signal / fréquence│
                       │ 📡 Spectre           │
                       │ ⭐ Favoris           │
                       └──────────┬───────────┘
                                  ▼
                           ▶️ VLC / libVLC
                                  ▼
                       🎧 Audio  /  📺 Vidéo
```

\* RDS : nom de la station et informations transmises avec le signal FM.

---

## 🧩 Organisation des sources

```
OpenWave/
│
├── src/openwave/
│   ├── core/              # Scanning engine, independent of any band
│   │   ├── frequency_manager.py  # Band plans, and splitting one into tuner-sized segments
│   │   ├── scanner.py            # Sweeping a band and collecting the measurements
│   │   ├── signal_detector.py    # Power spectrum, channel power, occupancy
│   │   └── units.py              # dBFS, frequency formatting
│   │
│   ├── radio/             # FM radio
│   │   ├── band.py               # FM band plans
│   │   ├── constants.py          # Deviation, pilot, de-emphasis
│   │   ├── demo.py               # An invented band, for trying it with no receiver
│   │   ├── fm_demodulator.py     # Quadrature demodulation, stereo, de-emphasis
│   │   ├── listener.py           # Continuous audio from a tuned station
│   │   ├── station.py
│   │   ├── station_detector.py   # Scan, then identify each station found
│   │   ├── synthesis.py          # Synthetic transmissions, for testing without hardware
│   │   └── rds/                  # RDS: blocks, groups, encoder, decoder
│   │
│   ├── tv/                # Digital television
│   │   ├── band.py               # UHF and VHF channel plans
│   │   ├── channel.py
│   │   ├── demo.py               # Invented multiplexes
│   │   ├── dvb_scanner.py
│   │   ├── mux_parser.py         # Collecting a multiplex's tables as they arrive
│   │   ├── psi.py                # PSI sections, their CRC and reassembly
│   │   ├── service_parser.py
│   │   ├── synthesis.py          # Synthetic multiplexes, for testing without a tuner
│   │   ├── tables.py             # PAT, PMT, SDT, NIT
│   │   └── ts.py                 # The 188-byte transport stream packet
│   │
│   ├── sdr/               # Receiver access
│   │   ├── device.py             # SdrDevice / DvbDevice interfaces
│   │   ├── device_manager.py     # Opening a receiver from a name like "rtlsdr:1"
│   │   ├── errors.py
│   │   ├── iq_file.py            # Recording and replaying captures
│   │   ├── linux_dvb.py          # Linux DVB API — never run on hardware
│   │   ├── mock.py               # Simulated receivers, used by the whole test suite
│   │   └── rtl_sdr.py            # RTL-SDR — never run on hardware
│   │
│   ├── media/             # Playback
│   │   ├── libvlc.py             # A live stream into libVLC through media callbacks
│   │   ├── stream.py
│   │   └── wav.py
│   │
│   ├── api/               # HTTP + WebSocket API
│   │   ├── app.py                # Routes, served under /api/v1
│   │   ├── models.py             # Request and response shapes
│   │   ├── scans.py              # Scans as background jobs
│   │   ├── streams.py            # Audio over HTTP
│   │   ├── spectrum.py           # Spectrum frames over a WebSocket
│   │   └── favourites.py         # Remembered stations and channels
│   │
│   └── cli.py             # `openwave` command line
│
├── frontend/              # Angular interface
├── scripts/               # Schema export, and the screenshots in this README
├── packaging/             # udev rules, Debian recipe
├── tests/
└── docs/                  # The documentation site
```

| Module | Rôle |
|---|---|
| `core` | Balaye les fréquences, mesure le signal, détecte et regroupe les services trouvés |
| `radio` | Démodule la FM, décode le RDS, identifie les stations |
| `tv` | Scanne les canaux DVB, lit les multiplex et en extrait les chaînes |
| `sdr` | Isole le matériel derrière une interface commune (RTL-SDR, DVB Linux, captures enregistrées, simulé) |
| `media` | Emballe un flux en direct en WAV et le lit avec libVLC |
| `api` | Expose le scan, les stations, les chaînes et les flux à l'interface |
| `frontend` | Interface Angular : liste, signal, spectre, favoris |

---

## 🧰 Technologies

- **Récepteurs** : RTL-SDR et tuners DVB-T Linux, plus les captures enregistrées et un
  simulateur. SoapySDR, qui apporterait HackRF et Airspy, **n'est pas implémenté** —
  c'est [matière à issue](docs/compatibility.md), pas une capacité actuelle.
- **Traitement du signal** : NumPy / SciPy — démodulation FM, décodage RDS, analyse de spectre
- **Média** : libVLC via `python-vlc`, qui tire un flux live par callbacks
- **API** : FastAPI, service local exposant les résultats du scan et les flux
- **Interface** : Angular

---

## 🗺️ Feuille de route

- [x] **v0.1** — scan de la bande FM et détection des stations (fréquence, puissance)
- [x] **v0.2** — démodulation FM et écoute via libVLC
- [x] **v0.3** — décodage RDS (nom de station, RadioText)
- [x] **v0.4** — scan DVB-T, lecture des multiplex et liste des chaînes
- [x] **v0.5** — API stable (`scan`, `stations`, `channels`, `streams`)
- [x] **v0.6** — interface Angular (liste, favoris, signal)
- [x] **v0.7** — visualisation du spectre en temps réel
- [x] **v1.0** — documentation complète, packaging pour Linux

Le découpage détaillé en tâches se trouve dans [TASKS.md](TASKS.md).

---

## 🌍 Pourquoi « OpenWave » ?

- **Open** : open source, transparent, extensible
- **Wave** : ondes radio, signaux, audio et transmission

Un nom court, technologique, assez large pour couvrir la FM, la TV et le SDR.

---

## ⚖️ Cadre d'usage

OpenWave est conçu pour recevoir des **diffusions publiques et libres d'accès**. Il ne vise pas à
contourner des protections, des chiffrements ou des services payants. Vérifie la réglementation
locale sur la réception radioélectrique avant utilisation. Voir [docs/legal.md](docs/legal.md).

---

## 📚 Documentation

La documentation est en anglais, comme le reste du dépôt.

| | |
|---|---|
| [Installing](docs/installing.md) | Ce qu'il faut installer, et ce que chaque extra apporte |
| [Using OpenWave](docs/using.md) | Du premier scan à l'écoute d'une station |
| [Command line](docs/cli.md) | Toutes les commandes et options |
| [HTTP API](docs/api.md) | Routes, WebSockets, et le schéma généré |
| [Hardware](docs/hardware.md) | Brancher un récepteur, droits d'accès, dépannage |
| [Compatibility](docs/compatibility.md) | Les récepteurs dont on sait qu'ils marchent |
| [Architecture](docs/architecture.md) | Comment les morceaux s'assemblent, et pourquoi |
| [Signals](docs/signals.md) | La radio et le traitement du signal derrière le code |
| [Contributing](docs/contributing.md) | Installation, vérifications, règles |

`mkdocs serve` la sert comme un site.

---

## 🤝 Contribuer

Le projet démarre et toutes les contributions sont bienvenues : code, tests avec du matériel réel,
documentation, traductions, idées.

**La contribution la plus utile est de faire tourner OpenWave sur un vrai récepteur et de dire ce
qui s'est passé**, même si ça a planté. Personne ne l'a fait. Voir
[la matrice de compatibilité](docs/compatibility.md) pour ce qui est connu et ce qui ne l'est pas.

Lis [CONTRIBUTING.md](CONTRIBUTING.md) pour installer l'environnement, puis cherche l'étiquette
[`good first issue`](https://github.com/Mxlione/Openwave/labels/good%20first%20issue).

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE).
