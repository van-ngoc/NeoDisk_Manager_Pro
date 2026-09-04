# Plan d'implémentation - Mode Production / Mode Physique

Passage de l'application **NeoDisk Manager** en **Mode Production / Mode Physique**, avec intégration d'un sélecteur de mode (Physique vs Virtuel/Simulation), détection et protection renforcée des disques physiques du système.

---

## 💡 Description du besoin

L'utilisateur souhaite passer l'application en **Mode Production / Mode Physique**.
Pour garantir un fonctionnement sur matériel physique tout en offrant un environnement de test sécurisé, nous allons :
1. Configurer le mode par défaut sur **Mode Physique / Production** (accès direct aux disques `/dev/sd*`, `/dev/nvme*`).
2. Ajouter un système de contrôle de mode dynamique (**Physique** vs **Virtuel/Simulation**).
3. Intégrer des barrières de sécurité avancées pour éviter l'effacement accidentel des disques système principaux (partitions `/`, `/boot`, etc.).
4. Mettre à jour l'interface Web (`index.html`) avec des indicateurs visuels clairs (Badge Mode Physique Pro, avertissements rouges, statut en direct).

---

## 🛠️ Modifications proposées

### [app.py](file:///home/ngoc/MX3_app/partition_manager/dossier_4/app.py)

- **Gestion des Modes** :
  - Variable globale `APP_MODE = 'production'` (Mode Physique par défaut).
  - Endpoints API `/api/mode` (GET/POST) pour consulter et basculer entre `production` (physique) et `simulation` (virtuel).

- **Mode Physique / Production (Actif)** :
  - Requête matérielle réelle via `lsblk` pour extraire tous les disques système (`/dev/sda`, `/dev/sdb`, `/dev/nvme0n1`, etc.), types de transport (USB, NVMe, SATA), points de montage et modèles.
  - Détection automatique des disques système critique (`is_system_disk`) montés sur `/`, `/boot`, `/home` ou `swap`.
  - Exécution réelle des commandes système (`parted`, `mkfs.ext4`, `mkfs.vfat`, `mkfs.ntfs`, `dd`, `umount`, `partprobe`) avec vérification des privilèges `sudo`.

- **Mode Virtuel / Simulation (Bac à sable)** :
  - Génération de périphériques virtuels de démonstration (Clé USB 32GB, SSD 500GB).
  - Simulation réaliste des opérations sans modifier le matériel physique.

---

### [templates/index.html](file:///home/ngoc/MX3_app/partition_manager/dossier_4/templates/index.html)

- **En-tête & UI** :
  - Ajout d'un badge de statut dynamique dans le header :
    - 🔴 **MODE PHYSIQUE / PRODUCTION (EN DIRECT)** quand le mode physique est actif.
    - 🟡 **MODE VIRTUEL / SIMULATION** en mode bac à sable.
  - Interrupteur (Toggle switch) pour permuter facilement entre Mode Physique et Mode Virtuel.

- **Liste des disques** :
  - Affichage de badges d'avertissement sur les disques système protégés (`DISQUE SYSTÈME - PROTÉGÉ`).
  - Détails matériels complets (Modèle, Série, Type de bus : NVMe/SATA/USB).

- **Sécurité des modales de confirmation** :
  - Alerte rouge renforcée lors d'une suppression / formatage en Mode Physique.
  - Obligation de confirmation explicite pour les disques physiques.

---

## 🔬 Plan de Vérification

### Tests automatisés & API
- Lancement de Flask `app.py`.
- Vérification du retour de `/api/mode` (`status: production`).
- Vérification du retour de `/api/disks` avec la liste des disques physiques remontés par `lsblk`.
- Test du basculement `/api/mode` (passage en simulation / retour en physique).

### Vérification Manuelle
- Ouverture de l'interface Web dans le navigateur.
- Vérification de l'affichage du Badge "MODE PHYSIQUE / PRODUCTION".
- Test de la bascule vers le Mode Virtuel et observation de l'actualisation dynamique des disques.
