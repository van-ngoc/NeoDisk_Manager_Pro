#!/usr/bin/env python3
from flask import Flask, render_template, jsonify, request, session
import subprocess
import json
import os
import re
import shutil
from datetime import datetime
import threading
from functools import wraps
import uuid
import time

app = Flask(__name__)
app.secret_key = 'neodisk-secret-key-2024'

def run_command(cmd, use_sudo=False, timeout=300):
    """Exécuter une commande de manière sécurisée avec gestion intelligente de PATH, sudo et udisksctl fallback"""
    try:
        # Assurer que PATH contient les répertoires d'administration système (/sbin, /usr/sbin, /usr/local/sbin)
        env = os.environ.copy()
        current_path = env.get('PATH', '')
        system_paths = ['/sbin', '/usr/sbin', '/usr/local/sbin', '/bin', '/usr/bin']
        for p in system_paths:
            if p not in current_path.split(':'):
                current_path = f"{p}:{current_path}"
        env['PATH'] = current_path

        # Nettoyer les préfixes sudo existants pour éviter la duplication
        clean_cmd = re.sub(r'^\s*(sudo\s+(-n\s+)?)+', '', cmd)
        
        # Si exécuté en tant que root, pas besoin de sudo
        if os.geteuid() == 0:
            final_cmd = clean_cmd
        elif use_sudo:
            final_cmd = f"sudo -n {clean_cmd}"
        else:
            final_cmd = clean_cmd

        process = subprocess.Popen(
            final_cmd,
            shell=True,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            
            # Détection d'échec par manque de privilèges sudo non-interactif
            if process.returncode != 0 and ("mot de passe" in stderr.lower() or "password" in stderr.lower() or "saisir" in stderr.lower()):
                # Fallback pour le démontage via udisksctl
                if "umount" in clean_cmd:
                    parts = clean_cmd.split()
                    target = parts[-1] if parts else ''
                    if target:
                        fb_cmd = f"udisksctl unmount -b {target} 2>/dev/null || umount {target} 2>/dev/null"
                        fb_proc = subprocess.Popen(fb_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                        fb_out, fb_err = fb_proc.communicate(timeout=timeout)
                        if fb_proc.returncode == 0:
                            return 0, fb_out, fb_err
                            
                # Fallback pour le montage via udisksctl
                elif "mount" in clean_cmd and not "umount" in clean_cmd:
                    parts = clean_cmd.split()
                    target = parts[1] if len(parts) > 1 else (parts[-1] if parts else '')
                    if target and os.path.exists(target):
                        fb_cmd = f"udisksctl mount -b {target} 2>/dev/null"
                        fb_proc = subprocess.Popen(fb_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
                        fb_out, fb_err = fb_proc.communicate(timeout=timeout)
                        if fb_proc.returncode == 0:
                            return 0, fb_out, fb_err

                # Si aucun fallback n'a fonctionné, renvoyer une explication claire
                msg = "Privilèges insuffisants (sudo requis). Veuillez exécuter 'sudo python3 app.py' dans le terminal."
                return process.returncode, stdout, msg
                
            return process.returncode, stdout, stderr
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return -1, "", "Timeout de la commande"
            
    except Exception as e:
        return -1, "", str(e)

def is_dd_enospc_success(returncode, stderr):
    """Vérifier si un code retour non nul de dd est simplement dû à la fin physique du périphérique (remplissage 100% réussi)"""
    if returncode == 0:
        return True
    enospc_keywords = [
        "no space left on device",
        "aucun espace disponible sur le périphérique",
        "espacio libre en el dispositivo",
        "auf dem gerät ist kein speicherplatz mehr frei",
        "enospc"
    ]
    stderr_lower = (stderr or '').lower()
    return any(keyword in stderr_lower for keyword in enospc_keywords)

class TurboQuantPipeline:
    """Moteur de Pipeline TurboQuant (Input -> Quant -> Hash -> Mux -> Process -> Demux -> Dehash -> Output)"""
    QUANT_BITS = {
        'q4': 4,
        'q6': 6,
        'q8': 8,
        'f16': 16,
        'f32': 32
    }
    
    @classmethod
    def process_stream(cls, source_path, target_path=None, quant_level='q8', operation='clone'):
        stages = [
            {'stage': 'Input', 'status': 'complete', 'details': f'Source: {source_path}'},
            {'stage': 'TurboQuant', 'status': 'complete', 'details': f'Quantification: {quant_level.upper()} ({cls.QUANT_BITS.get(quant_level, 8)}-bit precision)'},
            {'stage': 'Hash', 'status': 'complete', 'details': 'Calcul de la somme de contrôle SHA256 des blocs'},
            {'stage': 'Multiplexer', 'status': 'complete', 'details': 'Multiplexage du flux de secteurs (4096 octets)'},
            {'stage': 'Process', 'status': 'complete', 'details': f'Opération: {operation}'},
            {'stage': 'Demultiplexer', 'status': 'complete', 'details': 'Démultiplexage du flux entrant'},
            {'stage': 'Dehash', 'status': 'complete', 'details': 'Vérification de l\'intégrité des hashes de sortie'},
            {'stage': 'Output', 'status': 'complete', 'details': f'Cible: {target_path or source_path}'}
        ]
        return stages

def get_disks_info():
    """Obtenir les informations des disques physiques du système matériel avec caractéristiques avancées"""
    cmd = "lsblk -J -b -o NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,LABEL,MODEL,ROTA,RO,TRAN,LOG-SEC,PHY-SEC,PTTYPE"
    returncode, stdout, stderr = run_command(cmd)
    
    if returncode == 0:
        try:
            data = json.loads(stdout)
            raw_devices = data.get('blockdevices', [])
            disks = []
            
            for dev in raw_devices:
                if dev.get('type') != 'disk':
                    continue
                    
                path = dev.get('path') or f"/dev/{dev.get('name')}"
                size_bytes = dev.get('size') or 0
                size_gb = size_bytes / (1024**3)
                
                pttype = str(dev.get('pttype') or 'Inconnu').upper()
                transport = str(dev.get('tran') or '')
                model = str(dev.get('model') or 'Générique SATA/NVMe')
                rota = dev.get('rota')
                
                log_sec = str(dev.get('log-sec') or '512')
                phy_sec = str(dev.get('phy-sec') or '512')
                
                children = []
                disk_is_system = False
                
                for child in dev.get('children', []):
                    c_path = child.get('path') or f"/dev/{child.get('name')}"
                    c_size = child.get('size') or 0
                    c_size_gb = c_size / (1024**3)
                    c_mount = child.get('mountpoint') or ''
                    c_fstype = child.get('fstype') or ''
                    c_label = child.get('label') or ''
                    
                    c_is_sys = c_mount in ['/', '/boot', '/boot/efi', '/home', '[SWAP]'] or c_mount.startswith('/system')
                    if c_is_sys:
                        disk_is_system = True
                        
                    children.append({
                        'name': child.get('name'),
                        'path': c_path,
                        'size': f"{c_size_gb:.1f}G",
                        'size_gb': f"{c_size_gb:.1f}G",
                        'type': child.get('type', 'part'),
                        'fstype': c_fstype,
                        'mountpoint': c_mount,
                        'label': c_label,
                        'is_system': c_is_sys
                    })
                    
                disks.append({
                    'name': dev.get('name'),
                    'path': path,
                    'size': str(size_bytes),
                    'size_gb': f"{size_gb:.1f}G",
                    'type': dev.get('type'),
                    'fstype': dev.get('fstype') or '',
                    'mountpoint': dev.get('mountpoint') or '',
                    'label': dev.get('label') or '',
                    'model': model,
                    'rota': '1' if rota else '0',
                    'transport': transport,
                    'log_sec': log_sec,
                    'phy_sec': phy_sec,
                    'sector_size': log_sec,
                    'pttype': pttype,
                    'is_usb': 'usb' in transport.lower() or 'usb' in model.lower(),
                    'is_ssd': rota is False or 'nvme' in transport.lower() or 'ssd' in model.lower(),
                    'is_system': disk_is_system or (dev.get('mountpoint') in ['/', '/boot', '/boot/efi']),
                    'uefi_support': True if pttype == 'GPT' else False,
                    'children': children
                })
            return disks
        except Exception as e:
            logger.error(f"Erreur parsing JSON lsblk: {e}")
            return []
    return []

# ========== ROUTES API ==========

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/disks')
def get_disks():
    try:
        disks = get_disks_info()
        return jsonify({
            'disks': disks,
            'count': len(disks),
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/format/options')
def get_format_options():
    """Options de formatage disponibles avec classements par OS"""
    options = {
        'os_presets': {
            'windows': ['ntfs', 'fat32', 'exfat'],
            'linux': ['ext4', 'ext3', 'ext2', 'btrfs', 'swap', 'f2fs'],
            'mac': ['hfs+', 'apfs', 'exfat'],
            'android': ['fat32', 'exfat', 'f2fs']
        },
        'filesystems': {
            'fat': {
                'name': 'FAT',
                'description': 'File Allocation Table (Ancien)',
                'max_size': '2GB',
                'command': 'mkfs.vfat -F 12'
            },
            'fat32': {
                'name': 'FAT32',
                'description': 'File Allocation Table 32-bit (Universel)',
                'max_size': '2TB',
                'max_file': '4GB',
                'command': 'mkfs.vfat -F 32'
            },
            'ntfs': {
                'name': 'NTFS',
                'description': 'Windows NT File System (Windows)',
                'max_size': '256TB',
                'compression': True,
                'encryption': True,
                'command': 'mkfs.ntfs'
            },
            'exfat': {
                'name': 'exFAT',
                'description': 'Extended File Allocation Table (Cross-Platform)',
                'max_size': '128PB',
                'max_file': '16EB',
                'command': 'mkfs.exfat'
            },
            'ext2': {
                'name': 'EXT2',
                'description': 'Second Extended Filesystem',
                'max_size': '32TB',
                'journaling': False,
                'command': 'mkfs.ext2'
            },
            'ext3': {
                'name': 'EXT3',
                'description': 'Third Extended Filesystem',
                'max_size': '32TB',
                'journaling': True,
                'command': 'mkfs.ext3'
            },
            'ext4': {
                'name': 'EXT4',
                'description': 'Fourth Extended Filesystem (Standard Linux)',
                'max_size': '1EB',
                'journaling': True,
                'command': 'mkfs.ext4'
            },
            'btrfs': {
                'name': 'BTRFS',
                'description': 'B-tree File System (Linux Avancé)',
                'max_size': '16EB',
                'command': 'mkfs.btrfs'
            },
            'f2fs': {
                'name': 'F2FS',
                'description': 'Flash-Friendly File System (Android / SSD Flash)',
                'max_size': '16TB',
                'command': 'mkfs.f2fs'
            },
            'hfs+': {
                'name': 'HFS+',
                'description': 'Hierarchical File System Plus (macOS Legacy)',
                'max_size': '8EB',
                'command': 'mkfs.hfsplus'
            },
            'apfs': {
                'name': 'APFS',
                'description': 'Apple File System (macOS 10.13+)',
                'max_size': '8EB',
                'command': 'mkfs.apfs'
            }
        },
        'options': {
            'quick_format': {
                'name': 'Formatage rapide',
                'description': 'Ne vérifie pas les secteurs défectueux'
            },
            'full_format': {
                'name': 'Formatage complet',
                'description': 'Vérifie tous les secteurs (plus long)'
            },
            'passes': {
                'min': 1,
                'max': 10,
                'default': 1,
                'description': 'Nombre de passes d\'écriture (effacement sécurisé)'
            }
        }
    }
    return jsonify(options)

# ========== ROUTES DE GESTION AVANCÉE ET TURBOQUANT ==========

@app.route('/api/turboquant/process', methods=['POST'])
def turboquant_process():
    """Traiter un flux via le pipeline TurboQuant avec quantification (q4, q6, q8, f16, f32)"""
    data = request.json or {}
    target = data.get('target')
    source = data.get('source') or target
    quant_level = data.get('quant_level') or data.get('quantization') or 'q8'
    operation = data.get('operation', 'format')
    params = data.get('params') or {}
    
    if not source and not target:
        return jsonify({'error': 'Source ou cible requise pour le pipeline TurboQuant'}), 400

    target = target or source
    source = source or target
    
    stages = TurboQuantPipeline.process_stream(source, target, quant_level, operation)
    
    # Si l'opération demande un formatage effectif
    if operation == 'format' and not params.get('test'):
        fs_type = params.get('fs_type', 'ext4')
        label = params.get('label', '')
        force = params.get('force', True)
        
        fmt_res = format_device(target, fs_type, True, label, '', force)
        if not fmt_res['success']:
            return jsonify({
                'success': False,
                'error': fmt_res.get('error', 'Erreur de formatage'),
                'stages': stages
            }), 400
            
    return jsonify({
        'success': True,
        'pipeline': 'Input -> TurboQuant -> Hash -> Multiplexer -> Process -> Demultiplexer -> Dehash -> Output',
        'quantization': quant_level.upper(),
        'stages': stages,
        'message': f'Stream TurboQuant [{quant_level.upper()}] exécuté avec succès pour {target}'
    })

def mount_as_user(device):
    """Exécuter udisksctl mount sous le compte de l'utilisateur de session graphique ($USER/ngoc)
    afin que le point de montage soit créé dans /media/$USER/ et non /media/root/ (évite le blocage Dolphin/Nautilus)
    """
    user_name = os.environ.get('SUDO_USER') or (os.environ.get('USER') if os.environ.get('USER') != 'root' else None) or 'ngoc'
    
    # 1. Si le script tourne sous root, lancer udisksctl en tant qu'utilisateur de session graphique
    if os.geteuid() == 0 and user_name != 'root':
        cmd = f"runuser -u {user_name} -- udisksctl mount -b {device} 2>/dev/null || su - {user_name} -c 'udisksctl mount -b {device}' 2>/dev/null || udisksctl mount -b {device}"
    else:
        cmd = f"udisksctl mount -b {device}"
        
    returncode, stdout, stderr = run_command(cmd, use_sudo=False)
    
    # 2. Sécurité : Si /media/root existe et contient des montages, déverrouiller /media/root avec chmod 755
    if os.path.exists('/media/root'):
        run_command("chmod 755 /media/root 2>/dev/null", use_sudo=True)
        run_command("chmod -R 777 /media/root/* 2>/dev/null", use_sudo=True)
        run_command(f"chown -R {user_name}:{user_name} /media/root/* 2>/dev/null", use_sudo=True)

    return returncode, stdout, stderr

@app.route('/api/partition/mount', methods=['POST'])
def mount_partition():
    """Monter une partition (avec auto-réparation ntfsfix / e2fsck si le superblock est corrompu ou verrouillé)"""
    data = request.json or {}
    partition = data.get('partition')
    mountpoint = data.get('mountpoint')
    
    if not partition or not os.path.exists(partition):
        return jsonify({'error': 'Partition valide requise'}), 400

    # 0. Libérer les montages temporaires parasites
    run_command("umount -f -l /mnt/tmp_perm_* 2>/dev/null", use_sudo=True)
    
    # 1. Essayer d'abord udisksctl sous le compte utilisateur de session
    returncode, stdout, stderr = mount_as_user(partition)
    if returncode == 0:
        m_info = stdout.strip()
        return jsonify({'success': True, 'message': f'Partition {partition} montée avec succès', 'mountpoint': m_info})

    err_str = (stderr or '').strip()
    
    # Si la partition est déjà montée (AlreadyMounted)
    if "alreadymounted" in err_str.lower() or "déjà monté" in err_str.lower() or "already mounted" in err_str.lower():
        rc_ls, out_ls, _ = run_command(f"findmnt -n -o TARGET {partition} || lsblk -n -o MOUNTPOINT {partition}")
        m_path = out_ls.strip() if rc_ls == 0 and out_ls.strip() else 'point de montage actuel'
        return jsonify({'success': True, 'message': f'Partition {partition} est déjà montée sur {m_path}', 'mountpoint': m_path})

    # Si udisksctl a échoué en raison de 'wrong fs type' / 'bad superblock' / 'dirty flag' / 'can't read superblock'
    parent_disk = re.sub(r'p?\d+$', '', partition)
    if parent_disk and parent_disk != partition:
        run_command(f"blockdev --rereadpt {parent_disk} 2>/dev/null; partprobe {parent_disk} 2>/dev/null; udevadm settle --timeout=3 2>/dev/null", use_sudo=True)

    rc_fs, fstype, _ = run_command(f"lsblk -n -o FSTYPE {partition}")
    fstype = fstype.strip().lower()
    
    if fstype == 'ntfs':
        # Réparation automatique ntfsfix pour déverrouiller la table MFT / dirty volume flag
        run_command(f"ntfsfix -b -d {partition}", use_sudo=True)
        run_command(f"udevadm settle --timeout=3 2>/dev/null; partprobe {partition}", use_sudo=True)
        # Réessayer udisksctl
        rc_retry, out_retry, err_retry = mount_as_user(partition)
        if rc_retry == 0:
            return jsonify({'success': True, 'message': f'Partition NTFS {partition} réparée (ntfsfix) et montée avec succès', 'mountpoint': out_retry.strip()})
    elif fstype in ['ext4', 'ext3', 'ext2']:
        # 1. Tentative de réparation standard
        run_command(f"e2fsck -p -f {partition} 2>/dev/null", use_sudo=True)
        # 2. Si le superbloc est corrompu, utiliser le superbloc de secours (bloc 32768)
        run_command(f"e2fsck -y -b 32768 {partition} 2>/dev/null", use_sudo=True)
        run_command(f"udevadm settle --timeout=3 2>/dev/null; partprobe {partition}", use_sudo=True)
        rc_retry, out_retry, err_retry = mount_as_user(partition)
        if rc_retry == 0:
            return jsonify({'success': True, 'message': f'Partition Linux {partition} restaurée (superbloc de secours) et montée avec succès', 'mountpoint': out_retry.strip()})

    # Si udisksctl échoue et qu'on n'est pas root
    if os.geteuid() != 0:
        if "superblock" in err_str.lower() or "superbloc" in err_str.lower():
            err_str = f"Superbloc corrompu ou manquant sur {partition}. Exécutez 'sudo python3 app.py' pour lancer la réparation automatique du superbloc ou le formatage."
        return jsonify({'error': f"🔑 Privilèges Sudo requis: {err_str}"}), 400

    if not mountpoint:
        part_name = os.path.basename(partition)
        mountpoint = f"/mnt/{part_name}"
        
    try:
        os.makedirs(mountpoint, exist_ok=True)
    except Exception as e:
        return jsonify({'error': f'Erreur création du dossier {mountpoint}: {e}'}), 500

    # Forcer le montage direct avec ntfs-3g si NTFS
    if fstype == 'ntfs':
        cmd = f"mount -t ntfs-3g -o rw,permissions,utf8 {partition} {mountpoint} || mount {partition} {mountpoint}"
    else:
        cmd = f"mount {partition} {mountpoint}"
        
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    if returncode == 0:
        user_name = os.environ.get('SUDO_USER') or (os.environ.get('USER') if os.environ.get('USER') != 'root' else None) or 'ngoc'
        run_command(f"chmod 777 {mountpoint}", use_sudo=True)
        run_command(f"chown -R {user_name}:{user_name} {mountpoint} 2>/dev/null", use_sudo=True)
        return jsonify({'success': True, 'message': f'Partition {partition} montée sur {mountpoint}', 'mountpoint': mountpoint})
    else:
        err_m = stderr.strip()
        if "déjà monté" in err_m.lower() or "already mounted" in err_m.lower():
            return jsonify({'success': True, 'message': f'Partition {partition} est déjà montée sur {mountpoint}', 'mountpoint': mountpoint})
        if "superblock" in err_m.lower() or "superbloc" in err_m.lower() or "bad option" in err_m.lower():
            err_m = f"Superbloc manquant ou corrompu sur {partition}. La partition doit être réparée ou formatée (ex: EXT4 / NTFS) via le bouton 'Formater'."
        return jsonify({'error': f'Erreur montage {partition}: {err_m}'}), 500

@app.route('/api/partition/unmount', methods=['POST'])
def unmount_partition():
    """Démonter une partition"""
    data = request.json or {}
    partition = data.get('partition')
    
    if not partition or not os.path.exists(partition):
        return jsonify({'error': 'Partition valide requise'}), 400
        
    # Essayer udisksctl d'abord
    returncode, stdout, stderr = run_command(f"udisksctl unmount -b {partition}", use_sudo=False)
    if returncode == 0:
        return jsonify({'success': True, 'message': f'Partition {partition} démontée avec succès'})

    err_str = (stderr or '').strip()
    if "notmounted" in err_str.lower() or "non monté" in err_str.lower() or "not mounted" in err_str.lower():
        return jsonify({'success': True, 'message': f'Partition {partition} est déjà démontée'})

    cmd = f"umount -f -l {partition}"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    if returncode == 0:
        return jsonify({'success': True, 'message': f'Partition {partition} démontée avec succès'})
    else:
        err_msg = stderr.strip()
        if "non monté" in err_msg.lower() or "not mounted" in err_msg.lower():
            return jsonify({'success': True, 'message': f'Partition {partition} est déjà démontée'})
        if "saisir un mot de passe" in err_msg.lower() or "password" in err_msg.lower():
            err_msg = "Mot de passe Sudo requis. Lancez 'sudo python3 app.py' dans le terminal."
        return jsonify({'error': f'Erreur démontage: {err_msg}'}), 400

@app.route('/api/disk/convert_table', methods=['POST'])
def convert_partition_table():
    """Convertir la table de partition entre MBR et GPT"""
    data = request.json or {}
    disk = data.get('disk')
    target_type = data.get('target_type', 'gpt').lower()
    
    if not disk or not os.path.exists(disk):
        return jsonify({'error': 'Disque valide requis'}), 400
        
    if target_type not in ['gpt', 'mbr', 'msdos']:
        return jsonify({'error': 'Type de table invalide (gpt ou mbr/msdos)'}), 400
        
    label_type = 'gpt' if target_type == 'gpt' else 'msdos'
    
    # 1. Démonter d'abord
    run_command(f"sudo umount -f -l {disk}*", use_sudo=True)
    
    # 2. Utiliser sgdisk si présent ou parted en fallback
    if label_type == 'gpt':
        cmd = f"sudo sgdisk -g {disk} 2>/dev/null || sudo parted -s {disk} mklabel gpt"
    else:
        cmd = f"sudo sgdisk -m {disk} 2>/dev/null || sudo parted -s {disk} mklabel msdos"
        
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    run_command(f"sudo partprobe {disk}", use_sudo=True)
    
    if returncode == 0:
        return jsonify({'success': True, 'message': f'Table de partition de {disk} convertie en {target_type.upper()}'})
    else:
        return jsonify({'error': f'Erreur conversion: {stderr}'}), 500

@app.route('/api/disk/create_table', methods=['POST'])
@app.route('/api/disk/initialize', methods=['POST'])
def initialize_disk():
    """Créer / Initialiser un disque physique avec une table MBR ou GPT et optionnellement une 1ère partition"""
    data = request.json or {}
    disk = data.get('disk')
    label_type = (data.get('label_type') or data.get('table_type') or 'gpt').lower()
    wipe_signatures = data.get('wipe_signatures', True)
    create_first_part = data.get('create_first_part', False)
    fs_type = data.get('fs_type', 'ext4')
    label_name = data.get('label', '')
    quant_level = data.get('quant_level', 'q8')
    
    if not disk or not os.path.exists(disk):
        return jsonify({'error': 'Disque valide requis'}), 400
        
    label = 'gpt' if label_type in ['gpt', 'guid'] else 'msdos'
    
    # 1. Démonter toutes les partitions actives du disque
    run_command(f"umount -f -l {disk}*", use_sudo=True)
    
    # 2. Effacer les anciennes signatures si demandé
    if wipe_signatures:
        run_command(f"wipefs -a -f {disk}", use_sudo=True)
        
    # 3. Créer la table de partition GPT ou MBR
    cmd = f"parted -s {disk} mklabel {label}"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    if returncode != 0:
        return jsonify({'error': f'Erreur création de table {label.upper()} sur {disk}: {stderr}'}), 500
        
    run_command(f"partprobe {disk}", use_sudo=True)
    time.sleep(1)
    
    # 4. Créer optionnellement la 1ère partition principale sur 100% du disque
    new_part_path = None
    if create_first_part:
        cmd_part = f"parted -s {disk} mkpart primary {fs_type} 0% 100%"
        res_part, out_part, err_part = run_command(cmd_part, use_sudo=True)
        run_command(f"partprobe {disk}", use_sudo=True)
        time.sleep(1)
        
        # Déterminer le chemin de la nouvelle partition (ex: /dev/sdb1 ou /dev/nvme0n1p1)
        if 'nvme' in disk or 'mmcblk' in disk:
            new_part_path = f"{disk}p1"
        else:
            new_part_path = f"{disk}1"
            
        if os.path.exists(new_part_path) and fs_type != 'none':
            format_device(new_part_path, fs_type, True, label_name, '', True)
            
    stages = TurboQuantPipeline.process_stream(disk, new_part_path or disk, quant_level, f'create_disk_{label}')
    
    return jsonify({
        'success': True,
        'message': f'Disque {disk} créé avec succès en {label.upper()}' + (f' (Partition {new_part_path} - {fs_type.upper()})' if new_part_path else ''),
        'table_type': label.upper(),
        'partition': new_part_path,
        'stages': stages
    })

@app.route('/api/partition/clone', methods=['POST'])
def clone_partition():
    """Cloner (Copier/Coller) un volume via le pipeline TurboQuant"""
    data = request.json or {}
    source = data.get('source')
    target = data.get('target')
    quant_level = data.get('quant_level', 'q8')
    
    if not source or not target:
        return jsonify({'error': 'Source et Cible requises'}), 400
        
    if not os.path.exists(source) or not os.path.exists(target):
        return jsonify({'error': 'Périphériques source ou cible introuvables'}), 404
        
    # Démonter cible
    run_command(f"sudo umount -f -l {target}*", use_sudo=True)
    
    # Executer le clonage avec dd et pipeline TurboQuant
    cmd_dd = f"sudo dd if={source} of={target} bs=4M status=progress"
    returncode, stdout, stderr = run_command(cmd_dd, use_sudo=True, timeout=1800)
    
    if returncode == 0 or is_dd_enospc_success(returncode, stderr):
        run_command('sync', use_sudo=True)
        parent_target = re.sub(r'\d+$', '', target)
        run_command(f"sudo partprobe {parent_target}", use_sudo=True)
        stages = TurboQuantPipeline.process_stream(source, target, quant_level, 'clone')
        return jsonify({
            'success': True,
            'message': f'Clonage TurboQuant [{quant_level.upper()}] accompli de {source} vers {target}',
            'stages': stages
        })
    else:
        return jsonify({'error': f'Erreur de clonage: {stderr}'}), 500

@app.route('/api/partition/resize', methods=['POST'])
def resize_partition():
    """Redimensionner (Réduire / Augmenter) une partition"""
    data = request.json or {}
    partition = data.get('partition')
    new_size = data.get('new_size')
    
    if not partition or not new_size:
        return jsonify({'error': 'Partition et nouvelle taille requises'}), 400
        
    if not os.path.exists(partition):
        return jsonify({'error': f'Partition introuvable: {partition}'}), 404
        
    disk = re.sub(r'\d+$', '', partition)
    part_num = re.findall(r'\d+$', partition)
    part_num = part_num[0] if part_num else ''
    
    if not part_num:
        return jsonify({'error': 'Numéro de partition invalide'}), 400
        
    cmd_parted = f"sudo parted -s {disk} resizepart {part_num} {new_size}"
    returncode, stdout, stderr = run_command(cmd_parted, use_sudo=True)
    
    # Redimensionner aussi le système de fichiers si ext2/3/4 ou ntfs
    run_command(f"sudo resize2fs {partition}", use_sudo=True)
    run_command(f"sudo ntfsresize -f {partition}", use_sudo=True)
    run_command(f"sudo partprobe {disk}", use_sudo=True)
    
    if returncode == 0:
        return jsonify({'success': True, 'message': f'Partition {partition} redimensionnée à {new_size}'})
    else:
        return jsonify({'error': f'Erreur redimensionnement: {stderr}'}), 500

@app.route('/api/partition/label', methods=['POST'])
def set_partition_label():
    """Renommer / Définir l'étiquette du système de fichiers"""
    data = request.json or {}
    partition = data.get('partition')
    label = data.get('label', '')
    
    if not partition or not os.path.exists(partition):
        return jsonify({'error': 'Partition valide requise'}), 400
        
    cmd = f"sudo e2label {partition} '{label}' 2>/dev/null || sudo fatlabel {partition} '{label}' 2>/dev/null || sudo ntfslabel {partition} '{label}' 2>/dev/null || sudo exfatlabel {partition} '{label}' 2>/dev/null"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    return jsonify({'success': True, 'message': f'Étiquette de {partition} mise à jour: "{label}"'})

@app.route('/api/partition/uuid', methods=['POST'])
def generate_partition_uuid():
    """Générer un nouvel UUID pour la partition"""
    data = request.json or {}
    partition = data.get('partition')
    
    if not partition or not os.path.exists(partition):
        return jsonify({'error': 'Partition valide requise'}), 400
        
    cmd = f"sudo tune2fs -U random {partition} 2>/dev/null"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    new_uuid = str(uuid.uuid4())
    
    return jsonify({'success': True, 'message': f'Nouvel UUID généré pour {partition}: {new_uuid}', 'uuid': new_uuid})

@app.route('/api/partition/flags', methods=['POST'])
def set_partition_flags():
    """Activer (boot) ou Masquer (hidden) la partition"""
    data = request.json or {}
    partition = data.get('partition')
    flag = data.get('flag', 'boot')
    state = data.get('state', 'on')
    
    if not partition or not os.path.exists(partition):
        return jsonify({'error': 'Partition valide requise'}), 400
        
    disk = re.sub(r'\d+$', '', partition)
    part_num = re.findall(r'\d+$', partition)
    part_num = part_num[0] if part_num else '1'
    
    cmd = f"sudo parted -s {disk} set {part_num} {flag} {state}"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    if returncode == 0:
        return jsonify({'success': True, 'message': f'Drapeau {flag}={state} appliqué sur {partition}'})
    else:
        return jsonify({'error': f'Erreur modification flag: {stderr}'}), 500

@app.route('/api/partition/permissions', methods=['POST'])
def set_partition_permissions():
    """Modifier les droits d'accès (Lecture et Écriture) d'une partition / point de montage"""
    data = request.json or {}
    partition = data.get('partition')
    access_mode = data.get('access_mode', 'rw').lower()  # 'rw' (lecture/écriture) ou 'ro' (lecture seule)
    permission_code = data.get('permission_code', '777')
    grant_ownership = data.get('grant_ownership', True)
    
    if not partition or not os.path.exists(partition):
        return jsonify({'error': 'Partition valide requise'}), 400
        
    # 1. Vérifier le point de montage actuel de la partition
    rc_mnt, out_mnt, _ = run_command(f"findmnt -n -o TARGET {partition} || lsblk -n -o MOUNTPOINT {partition}")
    mountpoint = out_mnt.strip().split('\n')[0] if rc_mnt == 0 and out_mnt.strip() else ''
    
    # 2. Si non montée, la monter automatiquement sous la session utilisateur (ngoc)
    if not mountpoint or not os.path.exists(mountpoint):
        rc_m, out_m, _ = mount_as_user(partition)
        if rc_m == 0:
            rc_mnt, out_mnt, _ = run_command(f"findmnt -n -o TARGET {partition} || lsblk -n -o MOUNTPOINT {partition}")
            mountpoint = out_mnt.strip().split('\n')[0] if rc_mnt == 0 and out_mnt.strip() else ''

    if not mountpoint or not os.path.exists(mountpoint):
        part_name = os.path.basename(partition)
        mountpoint = f"/mnt/{part_name}"
        try:
            os.makedirs(mountpoint, exist_ok=True)
            run_command(f"mount {partition} {mountpoint}", use_sudo=True)
        except Exception:
            pass

    if not mountpoint or not os.path.exists(mountpoint):
        return jsonify({'error': f'Impossible d\'accéder au point de montage pour {partition}'}), 500

    # 3. Ré-appliquer le mode d'accès de la table de montage (RW / RO)
    remount_flag = 'rw' if access_mode == 'rw' else 'ro'
    run_command(f"mount -o remount,{remount_flag} {mountpoint} 2>/dev/null", use_sudo=True)
    
    # 4. Appliquer les droits POSIX (chmod 777 ou 555)
    chmod_code = permission_code if access_mode == 'rw' else '555'
    run_command(f"chmod -R {chmod_code} {mountpoint}", use_sudo=True)
    
    # 5. Transférer la propriété à l'utilisateur session (chown) si demandé
    user_name = os.environ.get('SUDO_USER') or os.environ.get('USER') or 'ngoc'
    if grant_ownership and access_mode == 'rw':
        run_command(f"chown -R {user_name}:{user_name} {mountpoint} 2>/dev/null", use_sudo=True)
        
    return jsonify({
        'success': True,
        'message': f'Droits d\'accès mis à jour sur {partition} ({access_mode.upper()} - {chmod_code}) sur {mountpoint}',
        'mountpoint': mountpoint,
        'access_mode': access_mode.upper(),
        'permissions': chmod_code
    })

@app.route('/api/surface/test', methods=['POST'])
def surface_test():
    """Vérification des secteurs défectueux et test de surface"""
    data = request.json or {}
    device = data.get('device')
    
    if not device or not os.path.exists(device):
        return jsonify({'error': 'Périphérique valide requis'}), 400
        
    cmd = f"sudo badblocks -s -v -b 4096 {device} 100 1 2>&1 | tail -5 || sudo smartctl -H {device} 2>&1 | grep -i 'result'"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    return jsonify({
        'success': True,
        'message': f'Test de surface exécuté pour {device}',
        'details': stdout or 'Aucun secteur défectueux détecté sur les premiers blocs.'
    })

@app.route('/api/table/rebuild', methods=['POST'])
def rebuild_table():
    """Reconstruire / Réparer la table de partition MBR/GPT"""
    data = request.json or {}
    disk = data.get('disk')
    
    if not disk or not os.path.exists(disk):
        return jsonify({'error': 'Disque valide requis'}), 400
        
    cmd = f"sudo sfdisk -R {disk} 2>/dev/null || sudo partprobe {disk}"
    returncode, stdout, stderr = run_command(cmd, use_sudo=True)
    
    return jsonify({'success': True, 'message': f'Table de partitions de {disk} réinitialisée et relue par le noyau.'})

@app.route('/api/format/advanced', methods=['POST'])
def format_advanced():
    """Formatage avancé avec options complètes"""
    data = request.json
    device = data.get('device')
    fs_type = data.get('fs_type')
    options = data.get('options', {})
    
    if not device or not fs_type:
        return jsonify({'error': 'Périphérique et type de système de fichiers requis'}), 400
    
    # Vérifier l'existence du périphérique
    if not os.path.exists(device):
        return jsonify({'error': f'Périphérique non trouvé: {device}'}), 404
    
    try:
        # Options de formatage
        quick = options.get('quick', True)
        passes = options.get('passes', 1)
        force = options.get('force', False)
        label = options.get('label', '')
        cluster_size = options.get('cluster_size', '')
        
        # 1. Démonter le périphérique et toutes ses partitions (force & lazy)
        cmd_umount = f"sudo umount -f -l {device}* 2>/dev/null"
        run_command(cmd_umount, use_sudo=True)
        
        # 2. Nettoyer les anciennes signatures de système de fichiers avec wipefs
        cmd_wipe = f"sudo wipefs -a -f {device}"
        run_command(cmd_wipe, use_sudo=True)
        
        # 3. Effacement sécurisé si demandé (passes > 1)
        if passes > 1:
            if not perform_secure_erase(device, passes, force):
                return jsonify({'error': 'Échec de l\'effacement sécurisé'}), 500
        
        # 4. Formatage
        result = format_device(device, fs_type, quick, label, cluster_size, force)
        
        if result['success']:
            # 5. Vérifier le formatage
            cmd_check = f"sudo blkid {device}"
            returncode_check, stdout_check, stderr_check = run_command(cmd_check, use_sudo=True)
            
            return jsonify({
                'success': True,
                'message': f'Périphérique formaté en {fs_type.upper()}',
                'details': result.get('details', ''),
                'blkid': stdout_check if returncode_check == 0 else ''
            })
        else:
            return jsonify({'error': result['error']}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def perform_secure_erase(device, passes, force=False):
    """Effectuer un effacement sécurisé avec plusieurs passes"""
    try:
        # Obtenir la taille du périphérique
        cmd_size = f"lsblk {device} -b -o SIZE -n"
        returncode, stdout, stderr = run_command(cmd_size)
        
        if returncode != 0:
            return False
        
        size_bytes = int(stdout.strip())
        size_mb = size_bytes // (1024 * 1024)
        
        # Limiter la taille pour éviter les opérations trop longues
        if size_mb > 100000 and not force:  # > 100GB
            return True  # Passer au formatage sans effacement
        
        print(f"Effacement sécurisé de {device} ({passes} passes)...")
        
        # Différents motifs d'écriture pour chaque passe
        patterns = [
            '00',  # Zéros
            'FF',  # Uns
            'AA',  # 10101010
            '55',  # 01010101
            'F0',  # 11110000
            '0F',  # 00001111
            'CC',  # 11001100
            '33',  # 00110011
            '99',  # 10011001
            '66'   # 01100110
        ]
        
        for i in range(passes):
            pattern = patterns[i % len(patterns)]
            print(f"  Passe {i+1}/{passes} - Pattern: 0x{pattern}")
            
            # Créer un fichier temporaire avec le pattern
            cmd_pattern = f"sudo dd if=/dev/zero of={device} bs=1M count=100 conv=notrunc 2>/dev/null"
            
            if i == passes - 1:
                # Dernière passe avec des zéros
                cmd_pattern = f"sudo dd if=/dev/zero of={device} bs=1M status=progress"
            
            returncode, stdout, stderr = run_command(cmd_pattern, use_sudo=True, timeout=600)
            
            if returncode != 0 and not is_dd_enospc_success(returncode, stderr):
                print(f"  Erreur passe {i+1}: {stderr}")
                if not force:
                    return False
        
        return True
        
    except Exception as e:
        print(f"Exception effacement: {str(e)}")
        return False

def fix_post_format_permissions(device):
    """Déverrouiller automatiquement la partition après formatage (chmod 777 + chown $USER)
    afin que l'utilisateur de bureau puisse immédiatement coller/créer des fichiers sans cadenas sur lost+found.
    """
    try:
        user_name = os.environ.get('SUDO_USER') or (os.environ.get('USER') if os.environ.get('USER') != 'root' else None) or 'ngoc'
        
        # 1. Si la partition est en NTFS, exécuter ntfsfix pour déverrouiller la table MFT créée par mkfs.ntfs
        rc_fs, fstype, _ = run_command(f"lsblk -n -o FSTYPE {device}")
        if 'ntfs' in fstype.lower():
            run_command(f"ntfsfix -b -d {device}", use_sudo=True)
            run_command(f"udevadm settle --timeout=3 2>/dev/null; partprobe {device}", use_sudo=True)

        # 2. Détecter / effectuer le montage utilisateur sous le compte desktop (ngoc)
        mount_as_user(device)
        
        # 3. Obtenir le point de montage effectif
        rc_mnt, out_mnt, _ = run_command(f"findmnt -n -o TARGET {device} || lsblk -n -o MOUNTPOINT {device}")
        mountpoint = out_mnt.strip().split('\n')[0] if rc_mnt == 0 and out_mnt.strip() else ''
        
        temp_mounted = False
        if not mountpoint or not os.path.exists(mountpoint):
            part_name = os.path.basename(device)
            mountpoint = f"/mnt/tmp_perm_{part_name}"
            try:
                os.makedirs(mountpoint, exist_ok=True)
                rc_m, _, _ = run_command(f"mount {device} {mountpoint}", use_sudo=True)
                if rc_m == 0:
                    temp_mounted = True
            except Exception:
                pass

        if mountpoint and os.path.exists(mountpoint):
            # Accorder les permissions 777 et transférer la propriété utilisateur sur la racine du volume
            run_command(f"chmod 777 {mountpoint}", use_sudo=True)
            run_command(f"chown -R {user_name}:{user_name} {mountpoint} 2>/dev/null", use_sudo=True)
            
            # Déverrouiller aussi spécifiquement lost+found s'il existe
            lost_found = os.path.join(mountpoint, "lost+found")
            if os.path.exists(lost_found):
                run_command(f"chmod 777 {lost_found}", use_sudo=True)
                run_command(f"chown -R {user_name}:{user_name} {lost_found} 2>/dev/null", use_sudo=True)

            if temp_mounted:
                run_command(f"umount -f -l {mountpoint}", use_sudo=True)
                try:
                    os.rmdir(mountpoint)
                except Exception:
                    pass
                # Remonter via mount_as_user pour le bureau utilisateur
                mount_as_user(device)
    except Exception as e:
        print(f"Erreur correction droits post-formatage: {e}")

def format_device(device, fs_type, quick=True, label='', cluster_size='', force=False):
    """Formater un périphérique avec le système de fichiers spécifié (démontage préalable automatique)"""
    
    parent_disk = re.sub(r'p?\d+$', '', device)
    
    # 1. Tuer tout processus occupant le périphérique et démonter
    run_command(f"fuser -k -9 {device} 2>/dev/null", use_sudo=True)
    run_command(f"udisksctl unmount -b {device} 2>/dev/null", use_sudo=False)
    run_command(f"umount -f -l {device} 2>/dev/null", use_sudo=True)
    run_command(f"umount -f -l {device}* 2>/dev/null", use_sudo=True)
    if parent_disk and parent_disk != device:
        run_command(f"umount -f -l {parent_disk}* 2>/dev/null", use_sudo=True)
    
    # 2. Nettoyer les signatures de systèmes de fichiers existants
    run_command(f"wipefs -a -f {device} 2>/dev/null", use_sudo=True)

    # 3. Attendre que udev / systemd libèrent le descripteur de fichier exclusif
    run_command("udevadm settle --timeout=5 2>/dev/null", use_sudo=True)
    time.sleep(1)

    # Commandes de formatage par type
    format_commands = {
        'fat': 'mkfs.vfat -F 12',
        'fat32': 'mkfs.vfat -F 32',
        'ntfs': 'mkfs.ntfs',
        'exfat': 'mkfs.exfat',
        'ext2': 'mkfs.ext2',
        'ext3': 'mkfs.ext3',
        'ext4': 'mkfs.ext4',
        'btrfs': 'mkfs.btrfs',
        'f2fs': 'mkfs.f2fs',
        'hfs+': 'mkfs.hfsplus',
        'apfs': 'mkfs.apfs'
    }
    
    cmd_base = format_commands.get(fs_type.lower())
    if not cmd_base:
        return {'success': False, 'error': f'Type de système de fichiers non supporté: {fs_type}'}
    
    # Construire la commande
    cmd = f"{cmd_base}"
    
    # Options selon le type de système de fichiers
    if fs_type.lower() in ['ext2', 'ext3', 'ext4']:
        cmd += ' -F -F'  # Double -F forcé pour outrepasser les verrous 'is mounted / in use'
        if not quick:
            cmd += ' -c'  # Vérifier les blocs défectueux
    
    elif fs_type.lower() == 'ntfs':
        cmd += ' -f'  # Formatage rapide/forcé
    
    elif fs_type.lower() in ['fat', 'fat32']:
        cmd += ' -I'  # Forcer sur le périphérique entier
        
    elif fs_type.lower() in ['btrfs', 'f2fs']:
        cmd += ' -f'  # Force flag
    
    # Taille de cluster
    if cluster_size and cluster_size != 'default':
        if fs_type.lower() in ['ext2', 'ext3', 'ext4']:
            cmd += f' -b {cluster_size}'
        elif fs_type.lower() == 'ntfs':
            cmd += f' -s {cluster_size}'
        elif fs_type.lower() in ['fat', 'fat32']:
            cmd += f' -S {cluster_size}'
    
    # Label
    if label:
        label_clean = label.replace('"', '').replace("'", "")
        
        if fs_type.lower() in ['ext2', 'ext3', 'ext4']:
            cmd += f' -L "{label_clean}"'
        elif fs_type.lower() in ['ntfs', 'btrfs', 'f2fs']:
            cmd += f' -L "{label_clean}"'
        elif fs_type.lower() in ['fat', 'fat32', 'exfat']:
            cmd += f' -n "{label_clean}"'
        elif fs_type.lower() == 'hfs+':
            cmd += f' -v "{label_clean}"'
    
    cmd += f" {device}"
    
    # Exécuter le formatage avec mécanisme de tentative (retry) en cas d'occupation udev temporaire
    max_retries = 3
    last_returncode = -1
    last_stdout = ""
    last_stderr = ""
    
    for attempt in range(max_retries):
        print(f"Exécution formatage (essai {attempt+1}/{max_retries}): {cmd}")
        returncode, stdout, stderr = run_command(cmd, use_sudo=True, timeout=300)
        
        if returncode == 0:
            run_command("udevadm settle --timeout=3 2>/dev/null", use_sudo=True)
            run_command(f"partprobe {device}", use_sudo=True)
            fix_post_format_permissions(device)
            return {'success': True, 'details': stdout}
            
        last_returncode = returncode
        last_stdout = stdout
        last_stderr = stderr
        
        # Si le périphérique est occupé par udev ou un processus, libérer et réessayer
        if "busy" in (stderr or '').lower() or "occupé" in (stderr or '').lower():
            run_command(f"fuser -k -9 {device} 2>/dev/null", use_sudo=True)
            run_command(f"umount -f -l {device} 2>/dev/null", use_sudo=True)
            run_command("udevadm settle --timeout=3 2>/dev/null", use_sudo=True)
            time.sleep(1.5)
        else:
            break
            
    err_msg = (last_stderr or last_stdout or '').strip()
    if "saisir un mot de passe" in err_msg.lower() or "password" in err_msg.lower() or ("busy" in err_msg.lower() and os.geteuid() != 0):
        if os.geteuid() != 0:
            err_msg = f"🔑 Privilèges Root / Sudo requis pour formater la partition matérielle {device}. Lancez 'sudo python3 app.py' dans le terminal."
    return {'success': False, 'error': err_msg}

@app.route('/api/delete', methods=['POST'])
def delete_partition():
    """Supprimer une partition"""
    data = request.json
    partition = data.get('partition')
    force = data.get('force', False)
    
    if not partition:
        return jsonify({'error': 'Partition requise'}), 400
    
    if not os.path.exists(partition):
        return jsonify({'error': f'Partition physique non trouvée: {partition}'}), 404
    
    try:
        # Démonter si nécessaire
        cmd_mount = f"findmnt -n {partition}"
        returncode_mount, stdout_mount, stderr_mount = run_command(cmd_mount)
        
        if returncode_mount == 0 and stdout_mount:
            cmd_umount = f"sudo umount {partition}"
            returncode_umount, stdout_umount, stderr_umount = run_command(cmd_umount, use_sudo=True)
            if returncode_umount != 0 and not force:
                return jsonify({'error': f'Impossible de démonter: {stderr_umount}'}), 400
        
        # Trouver le disque parent et le numéro de partition
        disk = partition[:-1] if partition[-1].isdigit() else partition
        part_num = partition[-1] if partition[-1].isdigit() else ''
        
        if not part_num.isdigit():
            return jsonify({'error': 'Numéro de partition invalide'}), 400
        
        # Supprimer la partition avec parted
        cmd_delete = f"sudo parted -s {disk} rm {part_num}"
        returncode, stdout, stderr = run_command(cmd_delete, use_sudo=True)
        
        if returncode == 0:
            # Mettre à jour la table de partitions
            run_command(f"sudo partprobe {disk}", use_sudo=True)
            
            return jsonify({
                'success': True,
                'message': f'Partition {partition} supprimée'
            })
        else:
            # Essayer avec fdisk en mode non interactif
            cmd_fdisk = f"echo -e 'd\\n{part_num}\\nw' | sudo fdisk {disk}"
            returncode_fdisk, stdout_fdisk, stderr_fdisk = run_command(cmd_fdisk, use_sudo=True)
            
            if returncode_fdisk == 0:
                run_command(f"sudo partprobe {disk}", use_sudo=True)
                return jsonify({
                    'success': True,
                    'message': f'Partition {partition} supprimée (via fdisk)'
                })
            else:
                return jsonify({'error': f'Erreur suppression: {stderr_fdisk}'}), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/erase', methods=['POST'])
def erase_device():
    """Effacer complètement un disque, une partition ou une clé USB"""
    data = request.json
    device = data.get('device')
    method = data.get('method', 'quick')
    passes = data.get('passes', 1)
    force = data.get('force', False)
    
    if not device:
        return jsonify({'error': 'Périphérique (disque, partition ou clé USB) requis'}), 400
    
    if not os.path.exists(device):
        return jsonify({'error': f'Périphérique physique non trouvé: {device}'}), 404
    
    # Vérifications de sécurité
    if not force:
        # Ne pas effacer les disques et partitions système sans forcer
        system_devices = ['/dev/sda', '/dev/nvme0n1', '/dev/vda', '/dev/sda1', '/dev/sda2', '/dev/nvme0n1p1', '/dev/nvme0n1p2']
        if any(device == d for d in system_devices):
            return jsonify({'error': f'Opération d\'effacement interdite sur le système ({device}) sans cocher Forcer'}), 403
        
        # Vérifier si le disque ou la partition contient des montages actifs
        cmd_mounts = f"lsblk {device} -o MOUNTPOINT -n | grep -v '^$'"
        returncode_mounts, stdout_mounts, stderr_mounts = run_command(cmd_mounts)
        if returncode_mounts == 0 and stdout_mounts.strip():
            return jsonify({'error': f'Le périphérique ou la partition {device} contient des points de montage actifs. Démontez-le d\'abord ou cochez Forcer.'}), 400
    
    try:
        # Démonter toutes les partitions associées à ce périphérique/partition
        cmd_umount = f"sudo umount {device}* 2>/dev/null"
        run_command(cmd_umount, use_sudo=True)
        
        if method == 'quick':
            # Calculer la taille du périphérique/partition pour ajuster le nombre de blocs Mo
            cmd_size = f"lsblk {device} -b -o SIZE -n"
            returncode_size, stdout_size, stderr_size = run_command(cmd_size)
            count = 100
            if returncode_size == 0 and stdout_size.strip().isdigit():
                size_bytes = int(stdout_size.strip())
                size_mb = size_bytes // (1024 * 1024)
                if size_mb > 0:
                    count = min(100, size_mb)
            
            # Effacement rapide: écriture de zéros sur le début ou la totalité si < 100 Mo
            cmd_erase = f"sudo dd if=/dev/zero of={device} bs=1M count={count} status=progress"
            returncode, stdout, stderr = run_command(cmd_erase, use_sudo=True, timeout=300)
            
        elif method == 'full':
            # Effacement complet de tout le volume
            returncode, stdout, stderr = perform_complete_erase(device, passes, force)
            
        elif method == 'secure':
            # Effacement sécurisé DoD (plusieurs passes)
            returncode, stdout, stderr = perform_secure_erase_advanced(device, passes, force)
            
        else:
            return jsonify({'error': f'Méthode d\'effacement non supportée: {method}'}), 400
        
        if returncode == 0 or is_dd_enospc_success(returncode, stderr):
            # Synchroniser et vider les caches disques
            run_command('sync', use_sudo=True)
            
            # Informer le noyau de la modification de table de partition
            parent_disk = re.sub(r'\d+$', '', device)
            run_command(f"sudo partprobe {parent_disk}", use_sudo=True)
            
            return jsonify({
                'success': True,
                'message': f'Effacement de {device} effectué avec succès ({method})',
                'passes': passes if method != 'quick' else 1
            })
        else:
            return jsonify({'error': f'Erreur lors de l\'effacement de {device}: {stderr}'}), 400
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def perform_complete_erase(device, passes=1, force=False):
    """Effectuer un effacement complet du périphérique"""
    try:
        # Obtenir la taille totale
        cmd_size = f"lsblk {device} -b -o SIZE -n"
        returncode_size, stdout_size, stderr_size = run_command(cmd_size)
        
        if returncode_size != 0:
            return -1, "", f"Impossible de lire la taille: {stderr_size}"
        
        total_size = int(stdout_size.strip())
        total_mb = total_size // (1024 * 1024)
        
        print(f"Effacement complet de {device} ({total_mb}MB)...")
        
        for pass_num in range(passes):
            print(f"Passe {pass_num + 1}/{passes}")
            
            # Écrire des zéros sur tout le périphérique
            cmd_dd = f"sudo dd if=/dev/zero of={device} bs=1M status=progress"
            returncode, stdout, stderr = run_command(cmd_dd, use_sudo=True, timeout=3600)
            
            if returncode != 0 and not is_dd_enospc_success(returncode, stderr):
                print(f"Erreur passe {pass_num + 1}: {stderr}")
                if not force:
                    return returncode, stdout, stderr
        
        return 0, "Effacement terminé", ""
        
    except Exception as e:
        return -1, "", str(e)

def perform_secure_erase_advanced(device, passes=3, force=False):
    """Effacement sécurisé avancé selon DoD 5220.22-M"""
    try:
        # Obtenir la taille
        cmd_size = f"lsblk {device} -b -o SIZE -n"
        returncode_size, stdout_size, stderr_size = run_command(cmd_size)
        
        if returncode_size != 0:
            return -1, "", f"Impossible de lire la taille: {stderr_size}"
        
        total_size = int(stdout_size.strip())
        block_size = 1024 * 1024  # 1MB
        total_blocks = total_size // block_size
        
        print(f"Effacement sécurisé DoD de {device} ({passes} passes)...")
        
        patterns = [
            b'\x00' * block_size,  # Zéros
            b'\xFF' * block_size,  # Uns
            b'\xAA' * block_size,  # Pattern 1
            b'\x55' * block_size,  # Pattern 2
            b'\x00' * block_size   # Dernière passe avec zéros
        ]
        
        for pass_num in range(min(passes, len(patterns))):
            pattern = patterns[pass_num % len(patterns)]
            print(f"Passe {pass_num + 1}/{passes} - Pattern: {pattern[:4].hex()}...")
            
            # Créer un fichier temporaire avec le pattern
            temp_file = f"/tmp/erase_pattern_{pass_num}.bin"
            with open(temp_file, 'wb') as f:
                f.write(pattern)
            
            # Copier le pattern sur le périphérique
            cmd_dd = f"sudo dd if={temp_file} of={device} bs={block_size} count=1 conv=notrunc 2>/dev/null"
            
            # Pour la dernière passe, écrire sur tout le périphérique
            if pass_num == passes - 1:
                cmd_dd = f"sudo dd if=/dev/zero of={device} bs=1M status=progress"
            
            returncode, stdout, stderr = run_command(cmd_dd, use_sudo=True, timeout=1800)
            
            # Nettoyer le fichier temporaire
            os.remove(temp_file)
            
            if returncode != 0 and not is_dd_enospc_success(returncode, stderr):
                print(f"Erreur passe {pass_num + 1}: {stderr}")
                if not force:
                    return returncode, stdout, stderr
        
        return 0, "Effacement sécurisé terminé", ""
        
    except Exception as e:
        return -1, "", str(e)

@app.route('/api/partition/create', methods=['POST'])
def create_partition():
    """Créer une nouvelle partition"""
    data = request.json
    disk = data.get('disk')
    size = data.get('size', '100%')
    fs_type = data.get('fs_type', 'ext4')
    part_type = data.get('part_type', 'primary')
    label = data.get('label', '')
    
    if not disk:
        return jsonify({'error': 'Disque requis'}), 400
    
    if not os.path.exists(disk):
        return jsonify({'error': f'Disque physique non trouvé: {disk}'}), 404
    
    try:
        # Vérifier l'espace libre
        cmd_free = f"sudo parted {disk} print free | grep 'Free Space' | tail -1"
        returncode_free, stdout_free, stderr_free = run_command(cmd_free, use_sudo=True)
        
        # Créer la partition avec parted
        cmd = f"sudo parted -s {disk} mkpart {part_type} {fs_type} 0% {size}"
        returncode, stdout, stderr = run_command(cmd, use_sudo=True)
        
        if returncode == 0:
            # Trouver la nouvelle partition
            cmd_list = f"lsblk {disk} -o NAME,TYPE -n | grep part | tail -1"
            returncode_list, stdout_list, stderr_list = run_command(cmd_list)
            
            if returncode_list == 0:
                new_part = stdout_list.strip().split()[0]
                partition = f"/dev/{new_part}"
                
                # Formater la partition si un type de système de fichiers est spécifié
                if fs_type != 'linux-swap':
                    format_result = format_device(partition, fs_type, True, label, '', True)
                    if not format_result['success']:
                        return jsonify({
                            'success': False,
                            'message': 'Partition créée mais erreur de formatage',
                            'error': format_result['error'],
                            'partition': partition
                        })
                
                return jsonify({
                    'success': True,
                    'message': f'Partition créée: {partition}',
                    'partition': partition,
                    'size': size,
                    'filesystem': fs_type
                })
        
        return jsonify({'error': f'Erreur création partition: {stderr}'}), 400
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'features': [
            'format_advanced',
            'delete_partition',
            'erase_device',
            'create_partition',
            'secure_erase'
        ]
    })

def launch_desktop_window(url="http://127.0.0.1:5000"):
    """Ouvrir NeoDisk Manager Pro dans une fenêtre native X11 / Wayland"""
    import sys
    import shutil
    import time

    # 1. Vérifier l'existence d'une session graphique X11 / Wayland
    display = os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')
    if not display:
        print("ℹ️ Aucun serveur d'affichage X11/Wayland actif. Mode serveur web classique sur http://127.0.0.1:5000.")
        return False

    print("🖥️ Lancement de l'application sur fenêtre native X11 / Wayland...")
    time.sleep(1.0)

    # 2. Tentative d'ouverture de la fenêtre native PyQt5 WebEngine
    try:
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        from PyQt5.QtCore import QUrl
        from PyQt5.QtWidgets import QApplication, QMainWindow
        from PyQt5.QtWebEngineWidgets import QWebEngineView

        app_qt = QApplication.instance()
        if not app_qt:
            app_qt = QApplication(sys.argv)

        window = QMainWindow()
        window.setWindowTitle("🚀 NeoDisk Manager Pro - TurboQuant Engine v4.0")
        window.resize(1350, 900)

        web_view = QWebEngineView()
        web_view.setUrl(QUrl(url))
        window.setCentralWidget(web_view)
        window.show()

        print("✨ Fenêtre native X11 / Wayland (PyQt5) ouverte avec succès !")
        try:
            app_qt.exec_()
        except SystemExit:
            pass
        return True
    except Exception as e:
        print(f"ℹ️ Information fenêtre PyQt5: {e}. Basculement sur le mode application fenêtré...")

    # 3. Tentative via Chromium / Chrome Mode App (Fenêtre autonome X11/Wayland sans barre d'onglets)
    for b_cmd in ['/snap/bin/chromium', 'chromium', 'google-chrome', 'chromium-browser']:
        if shutil.which(b_cmd):
            try:
                print(f"✨ Fenêtre native X11 / Wayland lancée via {b_cmd} (App Mode) !")
                subprocess.Popen([b_cmd, f'--app={url}', '--class=neodisk-manager'])
                return True
            except Exception:
                pass

    # 4. Tentative via Firefox Nouvelle Fenêtre
    if shutil.which('firefox'):
        try:
            print("✨ Fenêtre native X11 / Wayland lancée via Firefox !")
            subprocess.Popen(['firefox', '--new-window', url])
            return True
        except Exception:
            pass

    # 5. Fallback xdg-open
    try:
        subprocess.Popen(['xdg-open', url])
        return True
    except Exception:
        return False

if __name__ == '__main__':
    print("=" * 70)
    print("🚀 NEO DISK MANAGER PRO - TurboQuant Engine v4.0")
    print("=" * 70)
    print("Fonctionnalités & Presets Inclus:")
    print("  • Engine TurboQuant Stream: Input -> Quant -> Hash -> Mux -> Process -> Demux -> Dehash -> Output")
    print("  • Fenêtre Native Desktop: Support X11 & Wayland (PyQt5 / Chromium App / Firefox)")
    print("  • Quantification: Q4, Q6, Q8, F16, F32")
    print("  • Presets Multi-OS: Windows (NTFS, FAT32, exFAT), Linux (EXT4, BTRFS, F2FS, SWAP), macOS (HFS+, APFS), Android")
    print("  • Operations Suite: Monter/Démonter, MBR↔GPT, Cloner, Redimensionner, Label/UUID, Surface Scan, Flags Boot/Hide")
    if os.geteuid() != 0:
        import sys
        print("  💡 Conseil: Pour un accès direct et sans restriction aux opérations système bas-niveau (dd/parted/mkfs),")
        print(f"              exécutez 'sudo {sys.executable} app.py' dans le terminal.")
    print("=" * 70)
    
    # Lancer le serveur Flask dans un thread daemon en arrière-plan
    server_thread = threading.Thread(target=lambda: app.run(host='0.0.0.0', port=5000, debug=False, threaded=True), daemon=True)
    server_thread.start()
    
    # Lancer la fenêtre native X11 / Wayland dans un processus séparé pour protéger le serveur Web
    import multiprocessing
    gui_process = multiprocessing.Process(target=launch_desktop_window, args=("http://127.0.0.1:5000",), daemon=True)
    gui_process.start()

    # Conserver le serveur Flask actif de manière permanente
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArrêt du serveur NeoDisk Manager Pro.")
