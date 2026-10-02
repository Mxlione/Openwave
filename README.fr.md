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
| Couche d'abstraction matérielle (`sdr/`) | 🟡 en cours |
| Récepteurs simulés (IQ synthétiques, rejeu MPEG-TS) | 🟡 en cours |
| Scan de la bande FM (v0.1) | 🔴 à faire |
| Démodulation FM et écoute (v0.2) | 🔴 à faire |
| Décodage RDS (v0.3) | 🔴 à faire |
| Scan DVB-T (v0.4) | 🔴 à faire |
| API HTTP (v0.5) | 🔴 à faire |
| Interface Angular (v0.6) | 🔴 à faire |
| Spectre temps réel (v0.7) | 🔴 à faire |

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
                       │   MEDIA PROCESSING   │
                       │  FFmpeg / codecs     │
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
│   ├── core/              # Moteur de scan
│   │   ├── scanner.py
│   │   ├── frequency_manager.py
│   │   ├── signal_detector.py
│   │   └── service_discovery.py
│   │
│   ├── radio/             # Radio FM
│   │   ├── fm_demodulator.py
│   │   ├── rds_decoder.py
│   │   └── station_detector.py
│   │
│   ├── tv/                # Télévision numérique
│   │   ├── dvb_scanner.py
│   │   ├── mux_parser.py
│   │   ├── channel_detector.py
│   │   └── service_parser.py
│   │
│   ├── sdr/               # Accès aux récepteurs
│   │   ├── device.py      # Interfaces SdrDevice / DvbDevice
│   │   ├── rtl_sdr.py
│   │   ├── soapy_sdr.py
│   │   ├── mock.py        # Récepteurs simulés utilisés par les tests
│   │   └── device_manager.py
│   │
│   ├── media/             # Traitement et lecture
│   │   ├── ffmpeg.py
│   │   ├── codecs.py
│   │   └── libvlc.py
│   │
│   ├── api/               # API HTTP + WebSocket
│   │   ├── scan.py
│   │   ├── stations.py
│   │   ├── channels.py
│   │   └── streams.py
│   │
│   └── cli.py             # Ligne de commande `openwave`
│
├── frontend/              # Interface Angular
├── tests/
└── docs/
    ├── architecture.md
    └── legal.md
```

| Module | Rôle |
|---|---|
| `core` | Balaye les fréquences, mesure le signal, détecte et regroupe les services trouvés |
| `radio` | Démodule la FM, décode le RDS, identifie les stations |
| `tv` | Scanne les canaux DVB, lit les multiplex et en extrait les chaînes |
| `sdr` | Isole le matériel derrière une interface commune (RTL-SDR, SoapySDR, DVB, simulé) |
| `media` | Transforme les flux avec FFmpeg et les lit avec libVLC |
| `api` | Expose le scan, les stations, les chaînes et les flux à l'interface |
| `frontend` | Interface Angular : liste, signal, spectre, favoris |

---

## 🧰 Technologies

- **Récepteurs** : RTL-SDR, tuners DVB-T, autres SDR via SoapySDR
- **Traitement du signal** : NumPy / SciPy — démodulation FM, décodage RDS, analyse de spectre
- **Média** : FFmpeg, libVLC
- **API** : FastAPI, service local exposant les résultats du scan et les flux
- **Interface** : Angular

---

## 🗺️ Feuille de route

- [ ] **v0.1** — scan de la bande FM et détection des stations (fréquence, puissance)
- [ ] **v0.2** — démodulation FM et écoute via libVLC
- [ ] **v0.3** — décodage RDS (nom de station, RadioText)
- [ ] **v0.4** — scan DVB-T, lecture des multiplex et liste des chaînes
- [ ] **v0.5** — API stable (`scan`, `stations`, `channels`, `streams`)
- [ ] **v0.6** — interface Angular (liste, favoris, signal)
- [ ] **v0.7** — visualisation du spectre en temps réel
- [ ] **v1.0** — documentation complète, packaging pour Linux

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

## 🤝 Contribuer

Le projet démarre et toutes les contributions sont bienvenues : code, tests avec du matériel réel,
documentation, traductions, idées.

Lis [CONTRIBUTING.md](CONTRIBUTING.md) pour installer l'environnement, puis cherche l'étiquette
[`good first issue`](https://github.com/Mxlione/Openwave/labels/good%20first%20issue).

---

## 📄 Licence

MIT — voir [LICENSE](LICENSE).
