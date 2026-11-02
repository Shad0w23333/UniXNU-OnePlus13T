#!/usr/bin/env python3
"""Build the OnePlus 13T's arm64e source UFS runtime and its authenticated policy handoff.

build-sheng-source-storage.py with this board's drivers. The original IOStorageFamily and APFS stay in the kernel
collection. Only the public IOBlockStorageDevice implementation is compiled here; its metaclass is registered late below
the original class so the original storage driver can match the source-backed device. BootProgress's UfsLog.c stays the
log, so none of sheng's BootLog objects come along, and NativeKeyStoreEvents rides here because the minimal image has no
display archive. Binary interface qualification is separate.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, default=ROOT)
    parser.add_argument('--storage-source', type=Path)
    parser.add_argument('--policy-dir', type=Path,
                        help='Directory containing OS/ and SystemOS/ signed policies and verification.json')
    args = parser.parse_args()
    tree = args.tree.resolve()
    storage = (args.storage_source or tree / 'refer/IOStorageFamily').resolve()
    # The OS profile names the root images ([root]); kernel.py passes them in the environment.
    build_id = os.environ.get('UNIXNU_IPSW_BUILD')
    if not build_id:
        parser.error('UNIXNU_IPSW_BUILD is not set; run through unixnu build kernel')
    policies = args.policy_dir or tree / f'build/verified-root-{build_id}'
    if not args.policy_dir and not policies.is_dir():
        shipped = sorted(tree.glob(f'scripts/policies/*{build_id}'))
        if len(shipped) != 1:
            parser.error(f'no policy directory for {build_id}: pass --policy-dir or ship scripts/policies/*{build_id}')
        policies = shipped[0]
    policies = policies.resolve()
    if os.environ.get('UNIXNU_XNU_ARCH', 'arm64e') != 'arm64e':
        parser.error('This adapter requires an arm64e iPadOS profile')
    os.environ['UNIXNU_XNU_ARCH'] = 'arm64e'
    if int(os.environ.get('UNIXNU_IOS_VERSION', '0').split('.')[0]) < 26:
        parser.error('This adapter requires iPadOS 26 or later')
    build = helper('source_compile', ROOT / 'scripts/build-sheng-source-drivers.py')
    headers = helper('source_headers', ROOT / 'scripts/prepare-storage-headers.py')
    target = helper('source_target', ROOT / 'scripts/kernel_target.py')
    out = tree / 'build/pagani-source-storage'
    out.mkdir(parents=True, exist_ok=True)
    header_report = headers.prepare(storage, '7edb88fbae296fb7c8ce2f64e115e116e566d51c', out / 'headers')
    tables_in_userdata = os.environ.get('XNU_SOURCE_STORAGE_TABLES', '') == 'userdata'
    selected = ['OS', 'SystemOS']
    manifest_paths = [policies / name / 'manifest.bin' for name in selected]
    key = (policies / f'{selected[0]}/trusted-key.bin').read_bytes()
    if len(key) != 395 or any(key != (policies / f'{name}/trusted-key.bin').read_bytes() for name in selected):
        parser.error('All selected policies must share the same complete explicit owner anchor')
    manifests = []
    expected = {}
    for name, prefix in (('OS', 'UNIXNU_ROOT_OS'), ('SystemOS', 'UNIXNU_ROOT_SYSTEMOS')):
        digest, identity = os.environ.get(prefix + '_SHA256'), os.environ.get(prefix + '_UUID')
        if not (digest and identity):
            parser.error(f'the OS profile has no [root] {name.lower()}_image_sha256 and {name.lower()}_volume_uuid')
        expected[name] = (digest, identity.upper())
    for path in manifest_paths:
        verification = json.loads(path.with_name('verification.json').read_text())
        digest, identity = expected[path.parent.name]
        if (not verification['real_c_signature_and_block_verification'] or
                verification['image_sha256'] != digest or verification['volume_uuid'] != identity or
                verification['trusted_key_sha256'] != hashlib.sha256(key).hexdigest()):
            parser.error(f'{path.parent.name}: require complete verification of the actual {build_id} image')
        # With the tables in userdata only the 544-byte signed header goes into the boot image; NativeUfs reads
        # "<window>.vroot" from the root image and vr_open checks it against the header.
        header = path.with_name('manifest.header.bin')
        if tables_in_userdata and header.is_file():
            data = header.read_bytes()
            if len(data) != 544 or data[56:88].hex() != digest:
                parser.error(f'{header}: policy header does not describe the selected image')
            manifests.append(data)
        else:
            data = path.read_bytes()
            if len(data) < 544 or data[56:88].hex() != digest:
                parser.error(f'{path}: manifest does not describe the selected image')
            manifests.append(data[:544] if tables_in_userdata else data)
    cursor = 32 + 16 * len(manifests)
    table = bytearray()
    for data in manifests:
        table += struct.pack('<QQ', cursor, len(data))
        cursor += len(data)
    bundle = struct.pack('<8sQQQ', b'VROOTB01', 1, len(manifests), cursor) + table + b''.join(manifests)
    (out / 'root-policies.bin').write_bytes(bundle)
    # Only the independent trust anchor is linked into the kernel. The large signed hash tables are a separate
    # firmware handoff, authenticated in XNU.
    anchor = out / 'RootAnchor.c'
    anchor.write_text('#include <stdint.h>\nconst uint8_t verified_root_key[395] = {' +
                      ','.join(str(b) for b in key) + '};\n')
    drivers = tree / 'drivers'
    base = out / 'IOBlockStorageDevice.cpp'
    subprocess.run([sys.executable, drivers / 'NativeUfs/make-source-base.py', storage / 'IOBlockStorageDevice.cpp', base], check=True)
    obj = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    cpp = json.loads((obj / 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
    c = json.loads((obj / 'osfmk/DEVELOPMENT/startup.o.json').read_text().strip().rstrip(','))
    includes = ['-I', str(out / 'headers/include'), '-I', str(tree / 'kits/PaganiVerifiedRoot/include'), '-I', str(tree / 'kits'),
                '-I', str(drivers / 'NativeUfs'), '-I', str(tree / '.cache/xnu-work/osfmk')]
    # NATIVE_UFS_READ_ONLY with NATIVE_UFS_LOG_WRITES: as on iPadOS 17, the only write the kernel may issue is the log window.
    flags = ['-DNATIVE_UFS_SM8750=1', '-DUFS_ROOT_PARTITION="unixnu"', '-DNATIVE_UFS_READ_ONLY=1', '-DNATIVE_UFS_LOG_WRITES=1',
             '-DNATIVE_UFS_SOURCE_API=1', '-DNATIVE_VERIFIED_BLOCK=1', '-DNATIVE_VERIFIED_BLOCK_MULTI_POLICY=1',
             '-DNATIVE_VERIFIED_POLICY_HANDOFF=1', '-mgeneral-regs-only']
    sources = [base, drivers / 'NativeUfs/NativeUfs.cpp', drivers / 'NativeUfs/SourceStorage.cpp',
               drivers / 'NativeUfs/SourceRootPolicies.cpp', drivers / 'NativeUfs/SourceRootBinding.cpp',
               drivers / 'NativeRamBlock/NativeRamBlock.cpp', drivers / 'NativeKeyStoreEvents/NativeKeyStoreEvents.cpp',
               drivers / 'NativeKeyStoreEvents/KeyStoreProbe.cpp', tree / 'kits/PaganiVerifiedRoot/src/verified_root.c',
               tree / 'kits/PaganiVerifiedRoot/adapters/xnu_crypto.c', anchor]
    late = ('IOBlockStorageDevice.cpp', 'NativeUfs.cpp', 'NativeRamBlock.cpp', 'NativeKeyStoreEvents.cpp')
    objects, inputs = [], {}
    objcopy = shutil.which('llvm-objcopy') or '/opt/homebrew/opt/llvm/bin/llvm-objcopy'
    for source in sources:
        record = c if source.suffix == '.c' else cpp
        output = out / (source.stem + '.o')
        command = build.compile_command(record, source, output, includes, flags if source.suffix != '.c' else [])
        if source == base or source.name == 'NativeUfs.cpp':
            # The public source's own deprecation, and NativeUfs.cpp's four implicit conversions the iPadOS 17 build also has.
            command.append('-Wno-error')
        result = subprocess.run(command, cwd=record['directory'], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out / (source.stem + '.command.json')).write_text(json.dumps(command, indent=2) + '\n')
        (out / (source.stem + '.log')).write_text(result.stdout)
        if result.returncode:
            raise SystemExit(result.stdout[-6000:])
        target.check_object(output.read_bytes(), source.name)
        if source.name in late:
            registered = output.with_suffix('.late.o')
            subprocess.run([objcopy, '--remove-section=__DATA,__mod_init_func',
                            f'--globalize-symbol=__GLOBAL__sub_I_{source.name}', output, registered], check=True)
            output = registered
        objects.append(output)
        inputs[str(source)] = build.sha(source)
    archive = out / 'libPaganiSourceStorage.a'
    archive.unlink(missing_ok=True)
    subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
    report = dict(archive=str(archive), archive_sha256=build.sha(archive), sources=inputs,
                  headers=header_report, target=target.TRIPLE,
                  policy_bundle_sha256=build.sha(out / 'root-policies.bin'),
                  trusted_key_sha256=hashlib.sha256(key).hexdigest(), runtime_qualified=False)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(archive)


if __name__ == '__main__':
    main()
