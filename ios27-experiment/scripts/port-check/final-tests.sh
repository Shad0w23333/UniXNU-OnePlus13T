#!/bin/bash
# 全部主机测试，结果写到 final-tests.log
S=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/w
TOP=/Users/xiaochoumao/unixnu-13t; E=$TOP/ios27-experiment
T=$TOP/UniXNU/out-gpu/oneplus13t/ipados-17.7.11-21H461/oneplus13t/tree
SSL=/opt/homebrew/opt/openssl@3
export PATH=/opt/homebrew/opt/llvm/bin:/opt/homebrew/opt/lld/bin:$PATH
run() { echo "### $1"; shift; "$@" 2>&1 | tail -${TAILN:-1} | cut -c1-220; echo "rc=${PIPESTATUS[0]}"; }
echo "== PaganiVerifiedRoot"
run check.py python3 -B $E/kits/PaganiVerifiedRoot/tests/check.py --output $S/vr-test --openssl-prefix $SSL
echo "== PaganiDarwinBoot"
cd $E/drivers/PaganiDarwinBoot/Test
run test-adt python3 -B test-adt.py
run test-kernel-parser python3 -B test-kernel-parser.py
run test-packed-ramdisk python3 -B test-packed-ramdisk.py
clang -Wall -Wextra -I../DarwinBootPkg/Driver window-size.c -o $S/l/ws && run window-size $S/l/ws
python3 -I $S/l/parse-real.py $E/drivers/PaganiDarwinBoot $S/l/mach.l
bash $S/edk/build.sh > $S/edk/build.log 2>&1; echo "EDK2 rc=$?"
for v in "plain" "both --fileset --vroot" "mem --memory-size 0x20000000 --fileset --vroot"; do set -- $v; n=$1; shift; TAILN=2 run "qemu $n $*" python3 -B $S/edk/qemu-test.py $S $TOP $E/drivers/PaganiDarwinBoot final-$n "$@"; done
echo "== NativeUfs"
cd $E/drivers/NativeUfs/tests
TAILN=20 run check_root_path_host python3 -B check_root_path_host.py --output $S/ufs-test --openssl-prefix $SSL --mainline $S/base/drivers/NativeUfs
cat $S/ufs-test/RootBase.log $S/ufs-test/RootBaseMainline.log | cut -c1-200
TAILN=3 run check_legacy_descriptors python3 -B check_legacy_descriptors.py
echo "== NativeRamBlock"
cd $E/drivers/NativeRamBlock/Test
for t in direct persist seeddisk sparse; do clang++ -std=c++20 -fsanitize=address,undefined -g -I.. $t.cpp -o $S/ram-test/$t 2>/dev/null; run $t $S/ram-test/$t; done
for t in packed-hash seed-identity; do clang++ -std=c++20 -fsanitize=address,undefined -g -I.. $t.cpp -o $S/ram-test/$t 2>/dev/null; run $t $S/ram-test/$t $S/ram-test/seed.packed; done
echo "== PaganiArmCpu"
cd $E/drivers/PaganiArmCpu/Test
for t in psci-idle scmi-perf spi-gate; do clang++ -std=c++17 -Wall -Wextra -fsanitize=address,undefined -g -I.. $t.cpp -o $S/ram-test/$t 2>/dev/null; run $t $S/ram-test/$t; done
echo "== PaganiArmDTPlatform"
cd $E/drivers/PaganiArmDTPlatform/Test
TAILN=3 run check-platform-host python3 -B check-platform-host.py
echo "== NativeKeyStoreEvents"
cd $E/drivers/NativeKeyStoreEvents
run boundary python3 -B test_closed_boundary.py
run keybag python3 -B test_volatile_keybag.py
echo "== BootProgress"
cd $E/drivers/BootProgress/Test
for t in ramoops ufslog crash gunyah-wdog stall; do cc -std=c11 -O2 -Wall -Wextra -fsanitize=address,undefined $t.c -o $S/bp-test/$t 2>/dev/null; (cd $S/bp-test; run $t ./$t); done
cc -std=c11 -O1 -g -Wall -Wextra -fsanitize=address,undefined -I$E/drivers/PaganiDarwinBoot/DarwinBootPkg/Driver -I$T/build/boot-progress console.c -o $S/bp-test/console 2>/dev/null; (cd $S/bp-test; run console ./console)
for t in stall-threads ufslog-threads; do cc -std=c11 -O1 -g -Wall -Wextra -Werror -fsanitize=thread $t.c -o $S/bp-test/$t 2>/dev/null; (cd $S/bp-test; run $t ./$t); done
CPATH=$T/build/boot-progress run panic-entry python3 -B panic-entry.py $S/xnu-series --font $E/drivers/PaganiDarwinBoot/DarwinBootPkg/Driver
find $E/drivers $E/kits -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
echo DONE
