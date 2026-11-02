#!/usr/bin/env python3
"""给里程碑 N1 做一个占位 Data 种子：空 APFS 卷，不含任何 IPSW 内容。

N1 只验证内核入口、八核和 PAC。根卷找不到时内核停在等待根设备，走不到 Data 挂载，所以这个种子不会被用到，
它只是让 payload 步骤能通过（CONFIG_XNU_OWNER_DATA_MOUNT 要求有种子）。N2 必须换成 make-data-seed.py 从 OS
镜像生成的真种子，并重新编译内核，因为卷 UUID 编进了内核。

用法：make-n1-placeholder-seed.py UNIXNU-27目录 输出.dbr
打印卷 UUID，填进 defconfig 的 CONFIG_XNU_OWNER_DATA_VOLUME_UUID。
"""
import plistlib
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    root, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    if output.exists():
        raise SystemExit(f'{output} 已存在，卷 UUID 编进了内核，不覆盖')
    sys.path.insert(0, str(root / 'drivers/DarwinBoot/Test'))
    import packed_ramdisk
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='n1-seed-', dir=output.parent) as directory:
        work = Path(directory)
        raw = work / 'seed.cdr'
        subprocess.run(['hdiutil', 'create', '-quiet', '-size', '32m', '-layout', 'NONE', '-fs', 'APFS',
                        '-volname', 'Data', '-type', 'UDTO', raw], check=True)
        # 只把卷挂一次再卸下，确保超级块落盘。
        mount = work / 'mount'
        mount.mkdir()
        attached = plistlib.loads(subprocess.check_output(['hdiutil', 'attach', '-nobrowse', '-owners', 'off',
                                                           '-mountpoint', mount, '-plist', raw]))
        subprocess.run(['hdiutil', 'detach', '-quiet', attached['system-entities'][0]['dev-entry']], check=True)
        packed = work / 'data.dbr'
        packed_ramdisk.pack(raw, packed)
        _, extents = packed_ramdisk.regions(packed.read_bytes())
        identities = set()
        for _, data in extents:
            for at in range(0, len(data), 4096):
                block = data[at:at + 4096]
                if block[32:36] == b'APSB':
                    identities.add(str(uuid.UUID(bytes=bytes(block[240:256]))).upper())
        if len(identities) != 1:
            raise SystemExit(f'期望一个 APFS 卷 UUID，得到 {sorted(identities)}')
        packed.replace(output)
    print(identities.pop())


if __name__ == '__main__':
    main()
