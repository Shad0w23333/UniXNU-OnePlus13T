#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Copyright (c) 2026, UniXNU contributors. All rights reserved.
"""把早晨 morning-facts.sh（PULL_FW=1）拉回的 WLAN 固件整理成 PaganiWifi 的种子目录布局（make-wifi-firmware-seed.md 第 3 步）。

  make-wifi-firmware-dir.py PHONE_OUT DEST [--join-ssid NAME [--join-open]]
  make-wifi-firmware-dir.py - DEST --join-ssid NAME [--join-open] [--odm DIR]      （固件就地读 modem_a 时：只生成 join.txt 和 /odm 的两个文件）

PHONE_OUT 是 morning-facts.sh 的输出目录（里面有 D4-wlan-firmware-paths.txt、D5-wlan-firmware-sha256.txt 和 firmware/）。
DEST 新建（已存在就拒绝），得到的布局与 PaganiWifi WifiFirmware.c 在种子里找的名字一致：
  DEST/peach/amss20.bin、peach/bdwlan.elf（及 bdwlan.* 变体）、peach/regdb.bin、peach/phy_ucode20.elf、peach/aux_ucode20.elf、
  peach/qdss_trace_config*.cfg、tmel_peach_20.elf、cnss_softsku_peach.pfm（存在才有）、MANIFEST.json（每个文件的手机路径、大小、sha256）。
每个文件的 sha256 都和手机上 sha256sum 的结果（D5）核对，不一致就失败。--join-ssid 另写 join.txt（密码在终端提示输入）（无人值守加入用，
第一行网络名、第二行密码；只放本地种子，勿提交）。只读 PHONE_OUT，不连手机，不写手机。专有文件：DEST 只留本地，绝不进任何仓库。
安卓实际用的 BDF 和 regdb 在 /odm/etc/wifi/peach，内容和 modem_a 那份不同：PaganiWifi 对这两个文件优先读种子，所以种子里放 /odm 版本。
PHONE_OUT 里两份都有时取 /odm 的。--odm DIR 从本地目录（DIR/peach/bdwlan.elf、regdb.bin）取，sha256 和手机上的核对。
"""
import argparse
import getpass
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

# 手机上的基名 -> 种子里的相对名（WifiFirmware.c 的候选名；有 peach/ 前缀的放 peach/ 下）
WANTED = re.compile(r'^(amss20\.bin|bdwlan[a-z]?\.(elf|b0[a-z]|e[0-9a-f]+)|regdb\.bin|phy_ucode20\.elf|aux_ucode20\.elf|'
                    r'qdss_trace_config(_perf)?_v2\.cfg|tmel_peach_20\.elf|cnss_softsku_peach\.pfm|peach_(sec\.dat|rpr\.bin|dpr\.bin))$')
NO_PREFIX = {'tmel_peach_20.elf', 'cnss_softsku_peach.pfm'}
# PKX110_16.0.10.500 的 /odm/etc/wifi/peach（2026-10-31 手机上算的，phone/20261031-115842/D5-wlan-firmware-sha256.txt）。系统更新后要重新取。
ODM_FILES = {'peach/bdwlan.elf': '9b48bc68f20a26eff07a0b66e4fced32e3107cf080506b0bef0c38021a23836d',
             'peach/regdb.bin': '353df6eb96be7f4c8406f313020d2b52c55905908eacc056b880202801f4a429'}


def fail(message):
    sys.exit('make-wifi-firmware-dir: ' + message)


def join_text(args):
    """join.txt 的内容，或 None。密码在终端提示输入，不走命令行参数（不留在 shell 历史和进程列表里）。
    限制就是协议栈的：WPA2-PSK（CCMP）或开放网络，密码 8–63 个字符或 64 位十六进制；纯 WPA3（SAE）和强制 PMF 的网络连不上。"""
    if not args.join_ssid:
        return None
    ssid = args.join_ssid.encode()
    if not 1 <= len(ssid) <= 32 or b'\n' in ssid or b'\r' in ssid:
        fail('网络名要 1–32 字节，不能有换行')
    psk = '' if args.join_open else getpass.getpass('Wi-Fi 密码（不回显）：')
    data = psk.encode()
    hex64 = len(data) == 64 and all(c in b'0123456789abcdefABCDEF' for c in data)
    if not args.join_open and not (8 <= len(data) <= 63 or hex64) or b'\n' in data or b'\r' in data:
        fail('密码要 8–63 个字符或 64 位十六进制，不能有换行')
    return ssid + b'\n' + data + b'\n'


def write_join(dest, text, manifest):
    """join.txt 是明文密码：0600，只留本地，清单里不记摘要（短文本的摘要可以被穷举）。"""
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / 'join.txt'
    path.write_bytes(text)
    path.chmod(0o600)
    manifest['join.txt'] = dict(phone_path=None, bytes=len(text), sha256=None)
    print(f'join.txt 写到 {path}（明文密码，勿提交、勿外传带它的种子或镜像）')


def copy_odm(source, dest, manifest):
    """/odm 的两个文件拷进 DEST/peach，sha256 必须是手机上的。缺文件或对不上就失败：放错 BDF 影响发射功率和合规，不能悄悄继续。"""
    for relative, digest in ODM_FILES.items():
        path = source / relative
        if not path.is_file():
            fail(f'{path} 不存在：从手机 /odm/etc/wifi/{relative} 取回来')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            fail(f'{path} 的 sha256 不是手机上的 {digest}：系统更新过就重新取一份，并更新 ODM_FILES')
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o644)
        manifest[relative] = dict(phone_path='/odm/etc/wifi/' + relative, bytes=len(data), sha256=digest)
    print(f'{" 和 ".join(ODM_FILES)} 来自 {source}（安卓用的 /odm 版本）')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('phone_out', type=Path)
    parser.add_argument('dest', type=Path)
    parser.add_argument('--join-ssid')
    parser.add_argument('--join-open', action='store_true', help='开放网络，不问密码')
    parser.add_argument('--odm', type=Path, help='本地目录，有 peach/bdwlan.elf 和 peach/regdb.bin（手机 /odm/etc/wifi/peach 的文件）')
    args = parser.parse_args()
    join = join_text(args)
    if str(args.phone_out) == '-':
        if not join:
            fail('PHONE_OUT 写 - 时必须给 --join-ssid')
        manifest = {}
        write_join(args.dest, join, manifest)
        if args.odm:
            copy_odm(args.odm, args.dest, manifest)
        (args.dest / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
        return
    paths_file, sums_file, pulled = (args.phone_out / 'D4-wlan-firmware-paths.txt', args.phone_out / 'D5-wlan-firmware-sha256.txt',
                                     args.phone_out / 'firmware')
    for need in (paths_file, sums_file, pulled):
        if not need.exists():
            fail(f'{need} 不存在：先跑 PULL_FW=1 sh morning-facts.sh')
    if args.dest.exists():
        fail(f'{args.dest} 已存在，拒绝覆盖')
    phone_sums = {}
    for line in sums_file.read_text(errors='replace').splitlines():
        m = re.match(r'^([0-9a-f]{64})\s+(/\S+)$', line.strip())
        if m:
            phone_sums[m.group(2)] = m.group(1)
    manifest, seen = {}, {}
    for phone_path in [p.strip() for p in paths_file.read_text(errors='replace').splitlines() if p.strip().startswith('/')]:
        base = phone_path.rsplit('/', 1)[-1]
        if not WANTED.match(base):
            continue
        local = pulled / phone_path.lstrip('/').replace('/', '_')
        if not local.is_file():
            fail(f'{phone_path} 在 D4 里但没拉回来（{local}）')
        data = local.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if phone_sums.get(phone_path) != digest:
            fail(f'{phone_path}：本地 sha256 {digest} 与手机上的 {phone_sums.get(phone_path)} 不一致')
        relative = base if base in NO_PREFIX else 'peach/' + base
        if relative in seen and seen[relative] != digest:
            kept = manifest[relative]['phone_path']
            if not (phone_path.startswith('/odm/') or kept.startswith('/odm/')):
                fail(f'{relative} 在手机上有两份不同的文件：{kept} 与 {phone_path}，请人工挑选')
            if kept.startswith('/odm/'):         # 安卓用 /odm 那份（BDF、regdb）
                continue
        elif relative in seen:
            continue
        seen[relative] = digest
        target = args.dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
        target.chmod(0o644)
        manifest[relative] = dict(phone_path=phone_path, bytes=len(data), sha256=digest)
    if 'peach/amss20.bin' not in manifest:
        fail('没有 amss20.bin：PaganiWifi 第 5/6 阶段离不开它')
    if join:
        write_join(args.dest, join, manifest)
    if args.odm:
        copy_odm(args.odm, args.dest, manifest)
    (args.dest / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    total = sum(v['bytes'] for v in manifest.values())
    print(f'{len(manifest)} 个文件，共 {total} 字节 -> {args.dest}')
    for name, info in sorted(manifest.items()):
        print(f'  {name:40s} {info["bytes"]:>10} {info["phone_path"] or "(本地生成)"}')


if __name__ == '__main__':
    main()
