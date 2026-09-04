# 🚀 NeoDisk Manager Pro

**NeoDisk Manager Pro** est une application avancée de gestion de disques et de partitions alimentée par le moteur **TurboQuant Stream Engine v4.0**. Elle offre une interface web moderne et réactive associée à une fenêtre native de bureau (X11 / Wayland) permettant de créer, modifier, formater, redimensionner, cloner et vérifier vos disques durs et partitions en toute sécurité.

---

## 🌟 Fonctionnalités Principales

- **Moteur TurboQuant Stream Pipeline v4.0** :
  `Input ➔ Quantification ➔ Hash ➔ Multiplexeur ➔ Process ➔ Démultiplexeur ➔ Déhash ➔ Output`
  - Modes de quantification supportés : `Q4`, `Q6`, `Q8`, `F16`, `F32`.

- **Presets Multi-OS** :
  - 🪟 **Windows** : NTFS, FAT32, exFAT
  - 🐧 **Linux** : EXT4, BTRFS, F2FS, SWAP
  - 🍎 **macOS** : HFS+, APFS
  - 🤖 **Android**

- **Gestion Complète des Partitions & Disques** :
  - **Opérations** : Créer, Supprimer, Formater, Copier, Coller, Déplacer.
  - **Redimensionnement** : Augmenter et réduire la taille des partitions.
  - **Tables de Partitions** : Support complet **MBR** et **GPT**, initialisation et conversion dynamique (MBR ↔ GPT).
  - **Identifiants & Métadonnées** : Modification des points de montage, changement d'étiquette (Label), génération de nouveaux UUIDs, modification du numéro de série et d'ID de partition.
  - **Attributs & Drapeaux** : Masquer / Afficher les partitions, activer les drapeaux de démarrage (Boot flag).
  - **Diagnostic & Intégrité** : Test de surface des disques, vérification du système de fichiers, reconstruction du MBR / GPT.

- **Double Mode d'Exécution** :
  - 🔴 **Mode Physique / Production** : Accès direct aux périphériques matériels réels (`/dev/sd*`, `/dev/nvme*`) via privilèges root/sudo. Protection automatique intégrée des disques système critiques (`/`, `/boot`, `/home`, `swap`).
  - 🟡 **Mode Virtuel / Simulation** : Environnement bac à sable sécurisé pour tester les opérations sans risque d'altérer le matériel physique.

---

## 💾 Périphériques & Matériel Pris en Charge

- Disques durs traditionnels (HDD), disques à état solide (SSD), NVMe et SSHD.
- RAID matériel (RAID 0, RAID 1, RAID 5, RAID 10).
- Tous bus et interfaces reconnus par le noyau Linux : IDE, SATA, NVMe, iSCSI, SCSI, IEEE1394 (FireWire), USB 3.0 / 2.0 / 1.0.
- Clefs USB, cartes SD/MicroSD, cartes mémoire et disques amovibles.
- Support des disques MBR et GPT, y compris les volumes supérieurs à **16 To**.
- Support jusqu'à 128 disques durs par système.
- Tailles de secteur gérées : 512, 1024, 2048, 4096 octets.
- Disques virtuels pour VMware, VirtualBox et Virtual PC.
- Compatibilité UEFI / EFI Boot.

---

## 📋 Prérequis Système

Pour exécuter NeoDisk Manager Pro avec l'ensemble de ses fonctionnalités bas-niveau, installez les dépendances système requises sur votre distribution Linux (Debian/Ubuntu/Fedora/Arch) :

```bash
# Ubuntu / Debian / Mint
sudo apt update
sudo apt install -y python3 python3-venv python3-pip parted udisks2 e2fsprogs ntfs-3g dosfstools btrfs-progs util-linux

# Arch Linux / Manjaro
sudo pacman -S python parted udisks2 e2fsprogs ntfs-3g dosfstools btrfs-progs util-linux

# Fedora / RHEL
sudo dnf install parted udisks2 e2fsprogs ntfs-3g dosfstools btrfs-progs util-linux
```

---

## 🚀 Installation & Démarrage

### 1. Cloner / Accéder au projet

```bash
cd "NeoDisk Manager Pro"
```

### 2. Configurer l'environnement virtuel Python

```bash
# Créer le venv
python3 -m venv venv

# Activer le venv
source venv/bin/activate

# Installer les dépendances Python
pip install flask
```

### 3. Lancer l'application

#### Option A : Via le script d'amorceur (Recommandé)
```bash
./launch.sh
```

#### Option B : Démarrage direct en mode privilèges root (Mode Physique)
Pour un accès direct et complet aux opérations matérielles (`parted`, `mkfs`, `dd`) :
```bash
sudo ./venv/bin/python3 app.py
```

#### Option C : Démarrage utilisateur standard
```bash
./venv/bin/python3 app.py
```

L'application démarrera le serveur Flask sur `http://127.0.0.1:5000` et ouvrira automatiquement une fenêtre d'application native bureau.

---

## 🖥️ Intégration dans le Menu Application Linux (`.desktop`)

Pour ajouter **NeoDisk Manager Pro** dans votre menu d'applications Linux :

1. Rendre le script de lancement exécutable :
   ```bash
   chmod +x launch.sh
   ```

2. Copier le fichier `.desktop` dans le répertoire d'applications utilisateur :
   ```bash
   mkdir -p ~/.local/share/applications
   cp neodisk-manager-pro.desktop ~/.local/share/applications/
   ```

3. Mettre à jour la base de données du bureau (facultatif selon la distribution) :
   ```bash
   update-desktop-database ~/.local/share/applications/
   ```

Vous pourrez ainsi lancer **NeoDisk Manager Pro** directement depuis votre lanceur d'applications (GNOME, KDE Plasma, XFCE, Cinnamon, etc.).

---

## 📄 Licence & Crédits

- Développé pour la suite d'outils de gestion système **NeoDisk Manager Pro**.
- Dépendances : Flask, Python 3, Linux Partitioning Utilities (`parted`, `util-linux`, `udisksctl`).
